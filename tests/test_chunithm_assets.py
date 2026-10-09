import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from src import chunithm_assets as assets


def artwork_bytes(color=(123, 56, 210, 180)):
    output = io.BytesIO()
    Image.new("RGBA", (96, 64), color).save(output, format="PNG")
    return output.getvalue()


class ArtworkCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name)
        self.root = self.parent / "cache"
        self.now = 1_800_000_000.0
        self.cache = assets.ArtworkCache(self.root, clock=lambda: self.now)
        self.payload = artwork_bytes()
        network_patch = patch.object(assets.requests, "get", side_effect=AssertionError("Unexpected network"))
        network_patch.start()
        self.addCleanup(network_patch.stop)

    def cache_file(self, name, *, age=0, payload=None):
        self.root.mkdir(exist_ok=True)
        path = self.root / name
        path.write_bytes(self.payload if payload is None else payload)
        os.utime(path, (self.now-age, self.now-age))
        return path

    def response(self, payload=None):
        return SimpleNamespace(status_code=200, content=self.payload if payload is None else payload)

    def test_cache_hit_across_requests_and_process_restart_preserves_pixels(self):
        with patch.object(assets.requests, "get", return_value=self.response()) as get:
            first = self.cache.get("jacket", 2802)
            first.putpixel((0, 0), (0, 0, 0, 0))  # Consumers cannot mutate cached bytes.
            self.now += 42
            second = self.cache.get("jacket", 2802)
            restarted = assets.ArtworkCache(self.root, clock=lambda: self.now)
            third = restarted.get("jacket", 2802)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(second.tobytes(), third.tobytes())
        self.assertEqual(second.getpixel((0, 0)), (123, 56, 210, 180))
        self.assertEqual(second.size, (96, 64))
        self.assertEqual((self.root / "jacket_2802.img").stat().st_mtime, self.now)
        self.assertNotIn("Authorization", get.call_args.kwargs["headers"])
        self.assertNotEqual(get.call_args.kwargs.get("verify"), False)

    def test_collections_do_not_collide_with_each_other_or_jackets(self):
        with patch.object(assets.requests, "get", return_value=self.response()) as get:
            for kind in ("jacket", "icon", "plate", "character", "trophy"):
                self.assertIsNotNone(self.cache.get(kind, 19))
            for kind in ("jacket", "icon", "plate", "character", "trophy"):
                self.assertIsNotNone(self.cache.get(kind, 19))
        self.assertEqual(get.call_count, 5)
        self.assertEqual(len(list(self.root.glob("*.img"))), 5)

    def test_failed_download_retries_after_five_minutes_and_never_caches_placeholder(self):
        with patch.object(assets.requests, "get", return_value=SimpleNamespace(status_code=404)) as get:
            self.assertIsNone(self.cache.get("plate", 10))
            attempts = get.call_count
            self.now += 299
            self.assertIsNone(self.cache.get("plate", 10))
            self.assertEqual(get.call_count, attempts)
            self.now += 1
            get.return_value = self.response()
            self.assertIsNotNone(self.cache.get("plate", 10))
            self.assertEqual(get.call_count, attempts + 1)
        self.assertEqual(len(list(self.root.glob("*.img"))), 1)

    def test_failed_download_records_are_memory_bounded_and_expire(self):
        with patch.object(self.cache, "_download", return_value=(None, None)):
            for asset_id in range(2100):
                self.cache.get("icon", asset_id)
        self.assertEqual(len(self.cache._failures), 2048)
        self.now += 301
        self.cache.cleanup()
        self.assertFalse(self.cache._failures)

    def test_idle_cleanup_and_lru_ignore_non_cache_files_and_subdirectories(self):
        expired = self.cache_file("jacket_1.img", age=30*86400+1)
        recent = self.cache_file("jacket_2.img", age=100)
        foreign = self.cache_file("account.json", age=40*86400, payload=b"keep")
        bundled = self.parent / "mate"
        bundled.mkdir()
        (bundled / "logo.webp").write_bytes(b"keep")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "jacket_999.img").write_bytes(self.payload)
        self.cache.cleanup()
        self.assertFalse(expired.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(foreign.read_bytes(), b"keep")
        self.assertTrue((bundled / "logo.webp").exists())
        self.assertTrue((nested / "jacket_999.img").exists())

    def test_cache_hit_refreshes_last_use_before_capacity_eviction(self):
        oldest = self.cache_file("jacket_1.img", age=100)
        next_oldest = self.cache_file("jacket_2.img", age=50)
        self.cache.max_bytes = len(self.payload) * 2
        self.assertIsNotNone(self.cache.get("jacket", 1))
        self.now += 10
        with patch.object(assets.requests, "get", return_value=self.response()):
            self.assertIsNotNone(self.cache.get("jacket", 3))
        self.assertTrue(oldest.exists())
        self.assertFalse(next_oldest.exists())
        self.assertTrue((self.root / "jacket_3.img").exists())
        self.assertLessEqual(self.cache._bytes, self.cache.max_bytes)

    def test_startup_and_hourly_maintenance_trim_preexisting_cache(self):
        first = self.cache_file("jacket_1.img", age=300)
        self.cache_file("jacket_2.img", age=200)
        self.cache.max_bytes = len(self.payload)
        self.cache.cleanup()
        self.assertFalse(first.exists())
        extra = self.cache_file("jacket_3.img", age=100)
        self.cache.cleanup()
        self.assertTrue((self.root / "jacket_2.img").exists())
        self.now += assets.CLEANUP_INTERVAL_SECONDS
        self.cache.cleanup()
        self.assertFalse((self.root / "jacket_2.img").exists())
        self.assertTrue(extra.exists())

    def test_expired_hit_is_refetched_even_between_cleanup_scans(self):
        self.cache.max_idle = 10
        self.cache_file("icon_1.img")
        self.cache.cleanup()
        self.now += 11
        with patch.object(assets.requests, "get", return_value=self.response()) as get:
            self.assertIsNotNone(self.cache.get("icon", 1))
        self.assertEqual(get.call_count, 1)

    def test_corrupt_cache_and_invalid_network_content_fall_back_safely(self):
        self.cache_file("icon_10.img", payload=b"broken")
        with patch.object(assets.requests, "get", side_effect=[self.response(b"<html>error</html>"), self.response()]) as get:
            image = self.cache.get("icon", 10)
        self.assertIsNotNone(image)
        self.assertEqual(get.call_count, 2)
        self.assertEqual((self.root / "icon_10.img").read_bytes(), self.payload)

    def test_disk_error_does_not_prevent_rendering(self):
        with patch.object(assets.requests, "get", return_value=self.response()), \
             patch.object(assets.tempfile, "NamedTemporaryFile", side_effect=PermissionError("read-only")):
            self.assertIsNotNone(self.cache.get("icon", 1))
        self.assertFalse(list(self.root.glob("*.img")))

    def test_atomic_write_failure_removes_temporary_file(self):
        with patch.object(assets.requests, "get", return_value=self.response()), \
             patch.object(Path, "replace", side_effect=OSError("disk full")):
            self.assertIsNotNone(self.cache.get("icon", 1))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cleanup_only_removes_abandoned_cache_temporaries(self):
        old = self.cache_file(".download-abcd1234.tmp", age=3601)
        active = self.cache_file(".download-abcd1235.tmp", age=10)
        unrelated = self.cache_file("manual.tmp", age=3601)
        self.cache.cleanup()
        self.assertFalse(old.exists())
        self.assertTrue(active.exists())
        self.assertTrue(unrelated.exists())

    def test_invalid_ids_never_access_network_or_disk(self):
        for asset_id in ("../secret", "19", True, -1, None, {}, 10**11):
            self.assertIsNone(self.cache.get("jacket", asset_id))
        self.assertIsNone(self.cache.get("../outside", 19))
        self.assertFalse(self.root.exists())

    def test_symlink_is_not_read_or_removed(self):
        target = self.parent / "private.img"
        target.write_bytes(self.payload)
        self.root.mkdir()
        link = self.root / "icon_1.img"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("OS does not allow symlink creation")
        with patch.object(assets.requests, "get", return_value=self.response()):
            self.assertIsNotNone(self.cache.get("icon", 1))
        self.cache.max_bytes = 0
        self.cache.cleanup(force=True)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), self.payload)

    def test_redirected_root_is_never_read_written_or_cleaned(self):
        original = self.cache_file("icon_1.img", age=40*86400)
        with patch.object(self.cache, "_safe_root", return_value=False), \
             patch.object(assets.requests, "get", return_value=self.response(artwork_bytes((0, 0, 0, 0)))):
            self.assertEqual(self.cache.get("icon", 1).getpixel((0, 0)), (0, 0, 0, 0))
            self.cache.cleanup(force=True)
        self.assertEqual(original.read_bytes(), self.payload)

    def test_file_guard_rejects_parent_and_nested_paths(self):
        sibling = self.parent / "icon_1.img"
        sibling.write_bytes(self.payload)
        self.root.mkdir()
        nested = self.root / "nested"
        nested.mkdir()
        nested_file = nested / "icon_2.img"
        nested_file.write_bytes(self.payload)
        self.assertFalse(self.cache._regular_file(sibling))
        self.assertFalse(self.cache._regular_file(nested_file))
        self.cache._discard(sibling)
        self.cache._discard(nested_file)
        self.assertTrue(sibling.exists())
        self.assertTrue(nested_file.exists())

    def test_network_exception_is_cached_as_a_short_lived_miss(self):
        with patch.object(assets.requests, "get", side_effect=assets.requests.Timeout("timeout")) as get:
            self.assertIsNone(self.cache.get("plate", 1))
            count = get.call_count
            self.assertIsNone(self.cache.get("plate", 1))
            self.assertEqual(count, get.call_count)

    def test_oversized_entry_is_not_written_and_pixel_limit_is_enforced(self):
        self.cache.max_bytes = 1
        with patch.object(assets.requests, "get", return_value=self.response()):
            self.assertIsNotNone(self.cache.get("icon", 1))
        self.assertFalse(self.root.exists())
        with patch.object(assets, "MAX_ASSET_PIXELS", 5):
            with self.assertRaises(ValueError):
                self.cache._decode(self.payload)

    def test_same_asset_concurrent_requests_share_one_download(self):
        entered = threading.Event()
        release = threading.Event()

        def download(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.response()

        with patch.object(assets.requests, "get", side_effect=download) as get, \
             ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(self.cache.get, "jacket", 2802) for _ in range(6)]
            self.assertTrue(entered.wait(3))
            release.set()
            images = [future.result(timeout=5) for future in futures]
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len({id(image) for image in images}), 6)

    def test_concurrent_downloads_are_capped_at_six_across_callers(self):
        lock = threading.Lock()
        active = peak = 0

        def download(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.04)
            with lock:
                active -= 1
            return self.response()

        with patch.object(assets.requests, "get", side_effect=download), \
             ThreadPoolExecutor(max_workers=12) as pool:
            images = list(pool.map(lambda i: self.cache.get("jacket", i), range(12)))
        self.assertTrue(all(image is not None for image in images))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, assets.DOWNLOAD_WORKERS)

    def test_jacket_fallback_urls_are_not_repeated(self):
        with patch.object(assets.requests, "get", return_value=SimpleNamespace(status_code=404)) as get:
            self.assertIsNone(self.cache.get("jacket", 2802))
        urls = [call.args[0] for call in get.call_args_list]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), len(assets.ASSETS_BASE_URLS) * 2)


    def test_decoded_and_thumbnail_hits_do_not_repeat_decode_or_resize(self):
        with patch.object(assets.requests, "get", return_value=self.response()) as get, \
             patch.object(self.cache, "_decode", wraps=self.cache._decode) as decode, \
             patch.object(assets.ImageOps, "fit", wraps=assets.ImageOps.fit) as fit:
            first = self.cache.thumbnail("jacket", 1, 170)
            expected_pixels = first.tobytes()
            first.putpixel((0, 0), (0, 0, 0, 0))
            self.now += 42
            second = self.cache.thumbnail("jacket", 1, 170)
            original = self.cache.get("jacket", 1)
        self.assertEqual((get.call_count, decode.call_count, fit.call_count), (1, 1, 1))
        self.assertEqual(second.tobytes(), expected_pixels)
        self.assertEqual(original.size, (96, 64))
        self.assertEqual(second.size, (170, 170))
        self.assertEqual((self.root / "jacket_1.img").stat().st_mtime, self.now)

    def test_thumbnail_matches_full_quality_lanczos_for_each_card_size(self):
        with patch.object(assets.requests, "get", return_value=self.response()):
            original = self.cache.get("jacket", 1)
            for size in (170, 188, 584):
                result = self.cache.thumbnail("jacket", 1, size)
                expected = assets.ImageOps.fit(original, (size, size), method=getattr(Image, "Resampling", Image).LANCZOS)
                self.assertEqual(result.tobytes(), expected.tobytes())

    def test_memory_caches_are_byte_bounded_and_idle_entries_expire(self):
        cache = assets.ArtworkCache(self.root, clock=lambda: self.now, memory_bytes=100_000)
        with patch.object(assets.requests, "get", return_value=self.response()):
            for asset_id in range(10):
                cache.thumbnail("jacket", asset_id, 70)
        for memory in (cache._decoded, cache._thumbnails):
            self.assertLessEqual(memory.bytes, 50_000)
            self.assertNotIn(("jacket", 0), memory.items)
        self.now += 31*86400
        cache.cleanup(force=True)
        self.assertEqual(cache._decoded.bytes + cache._thumbnails.bytes, 0)

    def test_disk_eviction_also_invalidates_decoded_and_thumbnail_entries(self):
        with patch.object(assets.requests, "get", return_value=self.response()):
            self.cache.thumbnail("jacket", 1, 170)
        self.cache.max_bytes = 0
        self.cache.cleanup(force=True)
        self.assertFalse(self.cache._decoded.items)
        self.assertFalse(self.cache._thumbnails.items)

    def test_distinct_disk_hits_can_decode_concurrently(self):
        self.cache_file("jacket_1.img")
        self.cache_file("jacket_2.img")
        barrier = threading.Barrier(2)
        decode = self.cache._decode

        def simultaneous_decode(payload):
            barrier.wait(timeout=3)
            return decode(payload)

        with patch.object(self.cache, "_decode", side_effect=simultaneous_decode), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda i: self.cache.get("jacket", i), (1, 2)))
        self.assertTrue(all(result is not None for result in results))

    def test_invalid_thumbnail_arguments_do_not_fetch(self):
        for size in (-1, 0, 2049, "170", True):
            self.assertIsNone(self.cache.thumbnail("jacket", 1, size))
        self.assertIsNone(self.cache.thumbnail("jacket", "../private", 170))


if __name__ == "__main__":
    unittest.main()
