"""Bounded, persistent cache for public CHUNITHM artwork (never player data)."""

from concurrent.futures import Future, ThreadPoolExecutor
from collections import OrderedDict
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from PIL import Image, ImageOps
import requests


ASSETS_BASE_URLS = [
    "https://assets2.lxns.net",
    "https://assets.lxns.net",
    "https://static.maimai.lxns.net",
]
CACHE_ROOT = Path(__file__).resolve().parents[1] / "data" / "b30_asset_cache"
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_MEMORY_BYTES = 64 * 1024 * 1024
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


class _ImageLRU:
    """Byte-bounded decoded images; guarded by the owning ArtworkCache lock."""

    def __init__(self, budget):
        self.budget = budget
        self.bytes = 0
        self.items = OrderedDict()

    def discard(self, key):
        record = self.items.pop(key, None)
        if record is not None:
            self.bytes -= record[0].width * record[0].height * 4

    def get(self, key, now, max_idle):
        record = self.items.get(key)
        if record is None:
            return None
        image, used = record
        if now - used >= max_idle:
            self.discard(key)
            return None
        self.items[key] = (image, now)
        self.items.move_to_end(key)
        return image.copy()

    def put(self, key, image, now):
        size = image.width * image.height * 4
        if size > self.budget:
            return
        self.discard(key)
        while self.items and self.bytes + size > self.budget:
            self.discard(next(iter(self.items)))
        self.items[key] = (image.copy(), now)
        self.bytes += size


class ArtworkCache:
    """Last-use mtime eviction; writes are atomic, misses expire, loads coalesce.

    Only immediate, regular files with cache-owned names are ever removed.
    Cleanup is lazy: first use, then hourly while active, and immediately when
    a write exceeds the byte budget. An idle bot does not need a timer thread.
    """

    def __init__(self, root=CACHE_ROOT, *, max_bytes=MAX_CACHE_BYTES,
                 max_idle=MAX_IDLE_SECONDS, failure_ttl=FAILURE_TTL_SECONDS,
                 clock=None, memory_bytes=MAX_MEMORY_BYTES):
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
        # Separate budgets prevent large originals from evicting every small
        # card thumbnail during a long report. Neither cache contains scores.
        self._decoded = _ImageLRU(memory_bytes // 2)
        self._thumbnails = _ImageLRU(memory_bytes // 2)

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
                kind, asset_id = path.stem.split("_", 1)
                identity = (kind, int(asset_id))
                self._decoded.discard(identity)
                for key in list(self._thumbnails.items):
                    if key[:2] == identity:
                        self._thumbnails.discard(key)
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
        for memory in (self._decoded, self._thumbnails):
            for key, (_, used) in list(memory.items.items()):
                if now - used >= self.max_idle:
                    memory.discard(key)
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
            with self._lock:
                if not self._regular_file(path):
                    return None
                stat = path.stat()
                if now - stat.st_mtime >= self.max_idle or stat.st_size > MAX_ASSET_BYTES:
                    self._discard(path)
                    return None
                payload = path.read_bytes()
                self._touch(path, now)
            # Decompression is CPU work, not cache bookkeeping: different
            # artwork can decode concurrently instead of holding one global lock.
            return self._decode(payload)
        except (OSError, ValueError, Image.DecompressionBombError):
            with self._lock:
                self._discard(path)
            return None

    def _touch(self, path, now):
        try:
            if self._regular_file(path):
                os.utime(path, (now, now))
        except OSError:
            pass  # A read-only cache can still supply useful artwork.

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
            image = self._decoded.get(key, now, self.max_idle) if self._safe_root() else None
            if image is not None:
                self._touch(path, now)
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
            image = self._read(path, now)
            payload = None
            if image is None:
                image, payload = self._download(kind, asset_id, timeout)
            with self._lock:
                now = self._clock()
                if image is not None:
                    if payload is not None:
                        self._store(path, payload, now)
                    if self._safe_root():
                        self._decoded.put(key, image, now)
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

    def thumbnail(self, kind, asset_id, size):
        """Full-quality LANCZOS square, cached separately from full-size art."""
        if (kind not in _KINDS or type(asset_id) is not int or not 0 <= asset_id <= 10**10
                or type(size) is not int or not 1 <= size <= 2048):
            return None
        key = (kind, asset_id, size)
        with self._lock:
            now = self._clock()
            self._cleanup_locked(now)
            image = self._thumbnails.get(key, now, self.max_idle) if self._safe_root() else None
            if image is not None:
                self._touch(self.root / f"{kind}_{asset_id}.img", now)
                return image
        original = self.get(kind, asset_id)
        if original is None:
            return None  # Do not retain a placeholder as a successful thumbnail.
        image = ImageOps.fit(original, (size, size), method=getattr(Image, "Resampling", Image).LANCZOS)
        with self._lock:
            if self._safe_root():
                self._thumbnails.put(key, image, self._clock())
        return image


ARTWORK_CACHE = ArtworkCache()
