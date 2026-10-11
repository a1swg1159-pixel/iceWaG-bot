import io
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from src import chunithm_assets as shared
from src.maimai_assets import MaimaiArtworkCache, ROOT


class MaimaiAssetsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name)
        self.legacy = self.parent / "jacket"
        self.legacy.mkdir()
        self.cache = MaimaiArtworkCache(self.parent / "cache", legacy_root=self.legacy)
        data = io.BytesIO()
        Image.new("RGBA", (100, 100), "#abcdef").save(data, format="PNG")
        self.payload = data.getvalue()

    def test_legacy_cover_reused_without_network_and_never_deleted_by_eviction(self):
        legacy = self.legacy / "123.png"
        legacy.write_bytes(self.payload)
        with patch.object(shared.requests, "get", side_effect=AssertionError("network")):
            image = self.cache.thumbnail("jacket", 123, 184)
        self.assertEqual(image.size, (184, 184))
        self.assertEqual(image.getpixel((50, 50)), (171, 205, 239, 255))
        self.cache.max_bytes = 0
        self.cache.cleanup(force=True)
        self.assertEqual(legacy.read_bytes(), self.payload)
        self.assertFalse(list(self.cache.root.glob("*.img")))

    def test_public_art_requests_use_maimai_paths_and_no_auth(self):
        response = SimpleNamespace(status_code=200, content=self.payload)
        with patch.object(shared.requests, "get", return_value=response) as get:
            for kind in ("jacket", "plate", "icon"):
                self.assertIsNotNone(self.cache.get(kind, 23))
        self.assertEqual(get.call_count, 3)
        for call, kind in zip(get.call_args_list, ("jacket", "plate", "icon")):
            self.assertIn(f"/maimai/{kind}/", call.args[0])
            self.assertNotIn("Authorization", call.kwargs["headers"])
        self.assertNotEqual(ROOT / "cache", shared.CACHE_ROOT)

    def test_bad_legacy_cover_falls_back_to_public_download(self):
        (self.legacy / "123.png").write_bytes(b"broken")
        response = SimpleNamespace(status_code=200, content=self.payload)
        with patch.object(shared.requests, "get", return_value=response) as get:
            self.assertIsNotNone(self.cache.get("jacket", 123))
        get.assert_called_once()

    def test_negative_cache_avoids_repeated_network_and_allows_retry(self):
        now = [1000]
        self.cache._clock = lambda: now[0]
        with patch.object(shared.requests, "get", return_value=SimpleNamespace(status_code=404)) as get:
            self.assertIsNone(self.cache.get("icon", 55))
            attempts = get.call_count
            self.assertIsNone(self.cache.get("icon", 55))
            self.assertEqual(get.call_count, attempts)
            now[0] += 301
            get.return_value = SimpleNamespace(status_code=200, content=self.payload)
            self.assertIsNotNone(self.cache.get("icon", 55))
            self.assertEqual(get.call_count, attempts + 1)

    def test_unsupported_game_is_rejected(self):
        with self.assertRaises(ValueError):
            shared.ArtworkCache(self.parent / "other", game="../other")


if __name__ == "__main__":
    unittest.main()
