import unittest
from unittest.mock import patch

from PIL import Image

from src import chunithm_card_art as art


class CardArtTests(unittest.TestCase):
    layout = art.CardLayout(96, 64, 40, 5, 12, 6)

    def jacket(self, color="red"):
        return Image.new("RGBA", (40, 40), color)

    def test_cache_reuses_public_art_but_returns_independent_copies(self):
        cache = art.CardArtCache()
        with patch.object(art, "build_card_art", wraps=art.build_card_art) as build:
            first = cache.get(self.jacket(), self.layout)
            original = first.tobytes()
            first.paste((255, 255, 255, 255), (0, 0, 96, 64))
            second = cache.get(self.jacket(), self.layout)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(second.tobytes(), original)
        self.assertIsNot(first, second)

    def test_changed_pixels_or_layout_never_reuse_stale_surfaces(self):
        cache = art.CardArtCache()
        with patch.object(art, "build_card_art", wraps=art.build_card_art) as build:
            first = cache.get(self.jacket(), self.layout)
            second = cache.get(self.jacket("blue"), self.layout)
            third = cache.get(self.jacket(), art.CardLayout(100, 64, 40, 5, 12, 6))
        self.assertEqual(build.call_count, 3)
        self.assertNotEqual(first.tobytes(), second.tobytes())
        self.assertEqual(third.size, (100, 64))

    def test_byte_budget_evicts_least_recently_used_entry(self):
        surface_bytes = self.layout.width * self.layout.height * 4
        cache = art.CardArtCache(max_bytes=surface_bytes * 2)
        with patch.object(art, "build_card_art", wraps=art.build_card_art) as build:
            cache.get(self.jacket("red"), self.layout)
            cache.get(self.jacket("green"), self.layout)
            cache.get(self.jacket("red"), self.layout)
            cache.get(self.jacket("blue"), self.layout)
            self.assertEqual(cache.bytes, surface_bytes * 2)
            cache.get(self.jacket("red"), self.layout)
            self.assertEqual(build.call_count, 3)
            cache.get(self.jacket("green"), self.layout)
            self.assertEqual(build.call_count, 4)
        self.assertEqual(len(cache.items), 2)

    def test_idle_entries_expire_on_activity_and_hits_extend_lifetime(self):
        now = [0]
        cache = art.CardArtCache(max_idle=10, clock=lambda: now[0])
        with patch.object(art, "build_card_art", wraps=art.build_card_art) as build:
            cache.get(self.jacket("red"), self.layout)
            cache.get(self.jacket("blue"), self.layout)
            now[0] = 9
            cache.get(self.jacket("red"), self.layout)
            now[0] = 10
            cache.get(self.jacket("red"), self.layout)
            self.assertEqual(len(cache.items), 1)
            self.assertEqual(build.call_count, 2)
            now[0] = 20
            cache.get(self.jacket("red"), self.layout)
            self.assertEqual(build.call_count, 3)

    def test_oversize_surface_is_returned_without_retention(self):
        cache = art.CardArtCache(max_bytes=1)
        image = cache.get(self.jacket(), self.layout)
        self.assertEqual(image.size, (96, 64))
        self.assertEqual(cache.bytes, 0)
        self.assertFalse(cache.items)

    def test_clear_releases_all_public_surfaces(self):
        cache = art.CardArtCache()
        cache.get(self.jacket(), self.layout)
        cache.clear()
        self.assertEqual(cache.bytes, 0)
        self.assertFalse(cache.items)

    def test_feather_preserves_sharp_centre_and_fades_every_edge(self):
        mask = art.jacket_feather(170)
        self.assertEqual(mask.getpixel((85, 85)), 255)
        for xy in ((0, 85), (169, 85), (85, 0), (85, 169)):
            self.assertEqual(mask.getpixel(xy), 0)
        self.assertGreater(mask.getpixel((4, 85)), 0)
        self.assertLess(mask.getpixel((4, 85)), 255)
        self.assertLess(mask.getpixel((165, 85)), mask.getpixel((4, 85)))
        source = self.jacket((16, 177, 191, 255))
        copy = source.tobytes()
        result = art.build_card_art(source, self.layout)
        self.assertEqual(result.getpixel((25, 32)), (16, 177, 191, 255))
        self.assertEqual(source.tobytes(), copy)

    def test_native_shade_ramps_match_approved_preview(self):
        shade = art.shade_layer(565, 256)
        for x in range(0, 565, 7):
            for y in range(0, 256, 9):
                expected = round(78 + 44 * min(1, max(0, (x - 125) / 230))
                                 + 25 * max(0, (y - 170) / 86))
                self.assertLessEqual(abs(shade.getpixel((x, y))[3] - expected), 1)

    def test_transparent_and_non_square_jackets_render_safely(self):
        cache = art.CardArtCache()
        for source in (Image.new("RGBA", (80, 20)), Image.new("RGB", (80, 20), "white")):
            result = cache.get(source, self.layout)
            self.assertEqual(result.size, (96, 64))
            self.assertEqual(result.getpixel((0, 0))[3], 0)
            self.assertEqual(result.getpixel((50, 30))[3], 255)
        self.assertEqual(art.MAX_SURFACE_BYTES, 32 * 1024 * 1024)
        self.assertEqual(art.MAX_SURFACE_IDLE, 3600)


if __name__ == "__main__":
    unittest.main()
