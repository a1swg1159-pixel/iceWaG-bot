"""Bounded, persistent cache for public CHUNITHM artwork (never player data)."""

from concurrent.futures import Future, ThreadPoolExecutor
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from PIL import Image
import requests


ASSETS_BASE_URLS = [
    "https://assets2.lxns.net",
    "https://assets.lxns.net",
    "https://static.maimai.lxns.net",
]
CACHE_ROOT = Path(__file__).resolve().parents[1] / "data" / "b30_asset_cache"
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_IDLE_SECONDS = 30 * 86400
FAILURE_TTL_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 3600
DOWNLOAD_WORKERS = 6
MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_ASSET_PIXELS = 16_000_000
_KINDS = {"jacket", "plate", "icon", "character", "trophy"}
_CACHE_NAME = re.compile(r"(?:jacket|plate|icon|character|trophy)_[0-9]+\.img\Z")
_TEMP_NAME = re.compile(r"\.download-[a-z0-9_]+\.tmp\Z")
DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="chu-assets")


class ArtworkCache:
    """Last-use mtime eviction; writes are atomic, misses expire, loads coalesce.

    Only immediate, regular files with cache-owned names are ever removed.
    Cleanup is lazy: first use, then hourly while active, and immediately when
    a write exceeds the byte budget. An idle bot does not need a timer thread.
    """

    def __init__(self, root=CACHE_ROOT, *, max_bytes=MAX_CACHE_BYTES,
                 max_idle=MAX_IDLE_SECONDS, failure_ttl=FAILURE_TTL_SECONDS,
                 clock=None):
        self.root = Path(root).absolute()
        self.max_bytes = max_bytes
        self.max_idle = max_idle
        self.failure_ttl = failure_ttl
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(DOWNLOAD_WORKERS)
        self._inflight = {}
        self._failures = {}
        self._bytes = 0
        self._last_cleanup = float("-inf")

    def _safe_root(self):
        return not (self.root.is_symlink() or
                    getattr(self.root, "is_junction", lambda: False)())

    def _regular_file(self, path):
        # All paths are immediate children of this absolute, dedicated root.
        # Reject links rather than repeatedly resolving the whole Windows path
        # (surprisingly expensive for every cache hit); never traverse folders.
        return (path.parent == self.root and self._safe_root()
                and not path.is_symlink() and path.is_file())

    @staticmethod
    def _decode(payload):
        if not payload or len(payload) > MAX_ASSET_BYTES:
            raise ValueError("Invalid artwork length")
        with Image.open(io.BytesIO(payload)) as source:
            if source.width * source.height > MAX_ASSET_PIXELS:
                raise ValueError("Artwork exceeds pixel budget")
            return source.convert("RGBA")

    def _discard(self, path):
        try:
            if self._regular_file(path):
                size = path.stat().st_size
                path.unlink()
                self._bytes = max(0, self._bytes - size)
        except OSError:
            pass

    def cleanup(self, *, force=False):
        with self._lock:
            self._cleanup_locked(self._clock(), force=force)

    def _cleanup_locked(self, now, *, force=False):
        # Bound negative-cache memory even on a long-lived, busy bot.
        self._failures = {key: expiry for key, expiry in self._failures.items()
                          if expiry > now}
        if (not force and now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS
                and self._bytes <= self.max_bytes):
            return
        self._last_cleanup = now
        if not self._safe_root():
            return
        try:
            paths = list(self.root.iterdir())
        except OSError:
            return
        records = []
        for path in paths:
            try:
                if not self._regular_file(path):
                    continue
                stat = path.stat()
                if _TEMP_NAME.fullmatch(path.name):
                    if now - stat.st_mtime > CLEANUP_INTERVAL_SECONDS:
                        path.unlink()
                elif _CACHE_NAME.fullmatch(path.name):
                    records.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                continue
        self._bytes = sum(size for _, size, _ in records)
        for last_used, _, path in sorted(records):
            if now - last_used >= self.max_idle or self._bytes > self.max_bytes:
                self._discard(path)

    def _read(self, path, now):
        try:
            if not self._regular_file(path):
                return None
            stat = path.stat()
            if now - stat.st_mtime >= self.max_idle or stat.st_size > MAX_ASSET_BYTES:
                self._discard(path)
                return None
            image = self._decode(path.read_bytes())
        except (OSError, ValueError, Image.DecompressionBombError):
            self._discard(path)
            return None
        try:
            os.utime(path, (now, now))
        except OSError:
            pass  # A read-only cache can still supply useful artwork.
        return image

    def _store(self, path, payload, now):
        if len(payload) > self.max_bytes or not self._safe_root() or path.is_symlink():
            return
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            previous_size = path.stat().st_size if path.is_file() else 0
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".download-",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            os.utime(temporary, (now, now))
            temporary.replace(path)
            self._bytes += len(payload) - previous_size
            self._cleanup_locked(now)
        except OSError:
            pass  # Disk full/unwritable: return the downloaded image anyway.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _download(self, kind, asset_id, timeout):
        names = [f"{asset_id}.png"]
        if kind == "jacket":
            names.extend((f"{asset_id}.jpg", f"{asset_id:04d}.png"))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://maimai.lxns.net/",
                   "Accept": "image/png,image/jpeg,image/webp,*/*"}
        # No OAuth header/session is shared with public artwork hosts.
        with self._slots:
            for base in ASSETS_BASE_URLS:
                for name in dict.fromkeys(names):
                    response = None
                    try:
                        response = requests.get(f"{base}/chunithm/{kind}/{name}",
                                                headers=headers, timeout=(3, timeout),
                                                allow_redirects=True)
                        if response.status_code != 200:
                            continue
                        payload = response.content
                        return self._decode(payload), payload
                    except (requests.RequestException, OSError, ValueError,
                            Image.DecompressionBombError):
                        continue
                    finally:
                        close = getattr(response, "close", None)
                        if callable(close):
                            close()
        return None, None

    def get(self, kind, asset_id, *, timeout=5):
        if kind not in _KINDS or type(asset_id) is not int or not 0 <= asset_id <= 10**10:
            return None
        key = (kind, asset_id)
        path = self.root / f"{kind}_{asset_id}.img"
        with self._lock:
            now = self._clock()
            self._cleanup_locked(now)
            image = self._read(path, now)
            if image is not None:
                return image
            if self._failures.get(key, 0) > now:
                return None
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = self._inflight[key] = Future()
        if not owner:
            image = future.result()
            return image.copy() if image is not None else None
        try:
            image, payload = self._download(kind, asset_id, timeout)
            with self._lock:
                now = self._clock()
                if image is not None:
                    self._store(path, payload, now)
                    self._failures.pop(key, None)
                else:
                    if len(self._failures) >= 2048:
                        self._failures.pop(next(iter(self._failures)))
                    self._failures[key] = now + self.failure_ttl
                future.set_result(image)
            return image
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)


ARTWORK_CACHE = ArtworkCache()
