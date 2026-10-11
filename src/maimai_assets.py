"""Bounded cache of public maimai artwork; never OAuth tokens or player data."""
from pathlib import Path

from PIL import Image

from src.chunithm_assets import ArtworkCache, DOWNLOAD_POOL, MAX_ASSET_BYTES


ROOT = Path(__file__).resolve().parents[1] / "data" / "maimai_assets"


class MaimaiArtworkCache(ArtworkCache):
    def __init__(self, root=ROOT / "cache", *, legacy_root=ROOT / "jacket", **kwargs):
        super().__init__(root, game="maimai", **kwargs)
        self.legacy_root = Path(legacy_root) if legacy_root is not None else None

    def _download(self, kind, asset_id, timeout):
        # Existing user-downloaded covers remain useful and are never deleted.
        # New cached copies obey the shared byte/idle eviction policy.
        if kind == "jacket" and self.legacy_root is not None:
            path = self.legacy_root / f"{asset_id}.png"
            try:
                if path.is_file() and path.stat().st_size <= MAX_ASSET_BYTES:
                    payload = path.read_bytes()
                    return self._decode(payload), payload
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        return super()._download(kind, asset_id, timeout)


ARTWORK_CACHE = MaimaiArtworkCache()
