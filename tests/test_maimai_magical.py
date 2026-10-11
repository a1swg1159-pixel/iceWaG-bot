"""Offline regression checks for production MAGiCAL reports."""
from dataclasses import dataclass, replace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw

from src import maimai_render as preview


@dataclass
class Score:
    level_index: int = 3
    achievements: float = 100.1234
    song_name: str = "SAMPLE TRACK"
    song_type: str = "dx"
    level: str = "14.8"
    dx_rating: int = 321
    fc: str = "APP"
    fs: str = "FSDP"
    rank: str = ""

    def load_jacket(self):
        return Image.new("RGBA", (184, 184), "#699ba9")


@dataclass
class Player:
    name: str = "iceWaG"
    rating: int = 15555
    trophy: str = ""

    def load_icon(self):
        return None


class MagicalProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old, cls.new, cls.mixed = [Score()] * 35, [Score()] * 15, [Score()] * 10
        cls.player = Player()

    def test_original_card_size_for_all_difficulties_and_grades(self):
        for level in range(5):
            for achievement in (0, 50, 60, 70, 75, 80, 90, 94, 97, 98, 99, 99.5, 100, 100.5, 101):
                with self.subTest(level_index=level, achievements=achievement):
                    score = replace(self.mixed[0], level_index=level, achievements=achievement)
                    self.assertEqual(preview.card(score).size, (568, 236))

    def test_statistics_removed_entirely(self):
        before = preview.background(preview.W, 120)
        after = before.copy()
        calls = []
        original = preview.txt

        def record(*args, **kwargs):
            calls.append(str(args[2]))
            return original(*args, **kwargs)

        with patch.object(preview, "txt", side_effect=record):
            preview.section(after, 10, "BEST 35")
        self.assertEqual(calls, ["BEST 35"])
        self.assertIsNone(ImageChops.difference(before.crop((650, 0, preview.W, 120)),
                                                after.crop((650, 0, preview.W, 120))).getbbox())

    def test_b50_card_bounds_and_count(self):
        locations = []
        draw_grid = preview.draw_grid

        def record(image, scores, y):
            for i, _ in enumerate(scores):
                x = preview.LEFT + i % 5 * (preview.CW + preview.GAP)
                top = y + i // 5 * (preview.CH + preview.GAP)
                self.assertLessEqual(x + preview.CW, image.width - 32)
                self.assertLessEqual(top + preview.CH, image.height - 40)
                locations.append((x, top))
            return draw_grid(image, scores, y)

        with patch.object(preview, "draw_grid", side_effect=record):
            image = preview.b50(self.player, self.old, self.new)
        self.assertEqual(len(locations), 50)
        self.assertEqual(len(set(locations)), 50)
        self.assertEqual(image.size, (3000, 3110))

    def test_profile_matches_nameplate_aspect_and_fits_header(self):
        panel = preview.player_panel(self.player)
        self.assertEqual(panel.height, preview.PROFILE_H)
        self.assertLess(abs(panel.width / panel.height - 720 / 116), .01)
        self.assertLess(panel.width + preview.LEFT, 1690)
        self.assertLess(preview.PROFILE_H + 12, preview.HEADER)
        self.assertEqual(preview.player_panel(Player("冰冰冰舞萌测试名字", 15555, "maimai でらっくす")).size, panel.size)

    def test_rating_atlas_preserves_native_cells(self):
        with Image.open(preview.ASSETS / "profile_rating_digits.webp") as source:
            atlas = source.convert("RGBA")
        self.assertEqual(atlas.width % 4, 0)
        self.assertEqual(atlas.height % 4, 0)
        width, height = atlas.width // 4, atlas.height // 4
        for digit in range(10):
            glyph = preview.rating_digit(digit)
            self.assertEqual(glyph.size, (width, height))
            self.assertIsNotNone(glyph.getchannel("A").getbbox())
            x, y = digit % 4 * width, digit // 4 * height
            self.assertEqual(glyph.tobytes(), atlas.crop((x, y, x + width, y + height)).tobytes())

    def test_nickname_is_one_bold_text_run_with_natural_baseline(self):
        draw = Mock()
        preview.draw_nickname(draw, "iceWaG", (16, 0, 418, 64))
        draw.text.assert_called_once()
        args, kwargs = draw.text.call_args
        self.assertEqual(args[1], "iceWaG")
        self.assertEqual(kwargs["anchor"], "ls")
        self.assertEqual(kwargs["font"].getname()[1], "Bold")
        self.assertEqual(kwargs["font"].size, 42)
        self.assertNotIn("spacing", kwargs)
        face = kwargs["font"]
        reference = Image.new("RGBA", (450, 70))
        actual = reference.copy()
        box = face.getbbox("iceWaG", anchor="ls")
        baseline = (64 - (box[3] - box[1])) // 2 - box[1]
        ImageDraw.Draw(reference).text((16 - box[0], baseline), "iceWaG", font=face,
                                       anchor="ls", fill="#151719")
        preview.draw_nickname(ImageDraw.Draw(actual), "iceWaG", (16, 0, 418, 64))
        self.assertEqual(actual.tobytes(), reference.tobytes())

    def test_nickname_ink_stays_inside_field(self):
        for name in ("iceWaG", "gjpqy", "冰WaG節奏", "ＡＢＣ", "Nickname" * 20, ""):
            with self.subTest(name=name):
                image = Image.new("RGBA", (470, 100))
                preview.draw_nickname(ImageDraw.Draw(image), name, (16, 12, 418, 64))
                bounds = image.getchannel("A").getbbox()
                if bounds:
                    self.assertGreaterEqual(bounds[0], 16)
                    self.assertLessEqual(bounds[2], 434)
                    self.assertGreaterEqual(bounds[1], 12)
                    self.assertLessEqual(bounds[3], 76)

    def test_official_rating_band_boundaries(self):
        # Independent transcription of https://info-maimai.sega.jp/8674/.
        expected = (
            (0, 999, "white", 0), (1000, 1999, "blue", 0),
            (2000, 3999, "green", 0), (4000, 6999, "yellow", 0),
            (7000, 9999, "red", 0), (10000, 11999, "purple", 0),
            (12000, 12999, "bronze", 0), (13000, 13999, "silver", 0),
            (14000, 14249, "gold", 1), (14250, 14499, "gold", 2),
            (14500, 14749, "platinum", 1), (14750, 14999, "platinum", 2),
            (15000, 15249, "rainbow", 1), (15250, 15499, "rainbow", 2),
            (15500, 15749, "rainbow", 3), (15750, 15999, "rainbow", 4),
            (16000, 16249, "kiwami", 1), (16250, 16499, "kiwami", 2),
            (16500, 16749, "kiwami", 3), (16750, 99999, "kiwami", 4),
        )
        for lower, upper, color, stars in expected:
            for rating in (lower, upper):
                with self.subTest(rating=rating):
                    self.assertEqual(preview.rating_style(rating), (color, stars))

    def test_rating_assets_and_optional_stars_are_really_drawn(self):
        for rating, color, stars in preview.RATING_BANDS:
            with self.subTest(rating=rating):
                canvas = Image.new("RGBA", (720, 220))
                with patch.object(preview, "native_sprite", wraps=preview.native_sprite) as loader:
                    preview.draw_rating(canvas, rating, 220)
                expected = [f"profile_rating_{color}.webp"]
                if stars:
                    expected.append(f"profile_star_{stars}.webp")
                self.assertEqual([call.args[0] for call in loader.call_args_list], expected)
                self.assertIsNotNone(canvas.getchannel("A").getbbox())
        with self.assertRaises(ValueError):
            preview.draw_rating(Image.new("RGBA", (720, 220)), 100000, 220)

    def test_score_sheet_stays_fixed_width(self):
        shorter = preview.score_list(self.player, self.mixed[:5], "Lv. 14+")
        longer = preview.score_list(self.player, self.mixed, "Lv. 14+")
        self.assertEqual(shorter.width, 3000)
        self.assertEqual(longer.width, 3000)
        self.assertEqual(longer.height - shorter.height, preview.CH + preview.GAP)

    def test_dxrt_label_removed_but_numeric_rating_preserved(self):
        with patch.object(preview, "txt", wraps=preview.txt) as text:
            preview.card(Score(dx_rating=337))
        labels = [str(c.args[2]) for c in text.call_args_list]
        self.assertIn("337", labels)
        self.assertFalse(any("DX RT" in label or "DXRT" in label for label in labels))
        self.assertIn("100.1234", labels)

    def test_all_rank_sprites_are_present_and_same_visible_height(self):
        for key in ("d", "c", "b", "bb", "bbb", "a", "aa", "aaa",
                    "s", "sp", "ss", "ssp", "sss", "sssp"):
            with self.subTest(rank=key):
                item = preview.sprite(f"grade_{key}.webp")
                self.assertIsNotNone(item)
                self.assertLessEqual(round(item.width * 37 / item.height), 104)

    def test_lxns_sync_keys_map_to_actual_dx_badges(self):
        for key, expected in (("FSD", "fdx"), ("FSDP", "fdxp"), ("FS+", "fsp"),
                              ("SYNC", "sync"), ("FDX+", "fdxp"), ("../x", "")):
            self.assertEqual(preview.badge_key(key, sync=True), expected)
        with patch.object(preview, "fit_sprite", wraps=preview.fit_sprite) as fit:
            preview.card(Score())
        names = [c.args[1] for c in fit.call_args_list]
        self.assertEqual(names, ["mode_dx.webp", "badge_app.webp", "badge_fdxp.webp"])

    def test_missing_assets_fall_back_without_loading_demo_collections(self):
        with patch.object(preview, "raw_sprite", return_value=None), \
             patch.object(preview, "sprite", return_value=None):
            self.assertEqual(preview.card(Score()).size, (568, 236))
            self.assertEqual(preview.player_panel(Player()).height, 220)
        self.assertFalse((preview.ASSETS / "profile_plate.webp").exists())
        self.assertFalse((preview.ASSETS / "profile_icon.webp").exists())

    def test_real_profile_images_and_trophy_are_used(self):
        player = Player(trophy="TITLE FROM API")
        player.load_icon = Mock(return_value=Image.new("RGBA", (128, 128), "#ff0000"))
        player.load_name_plate_image = Mock(return_value=Image.new("RGBA", (720, 116), "#0000ff"))
        with patch.object(preview.ImageDraw.ImageDraw, "text", autospec=True) as text:
            panel = preview.player_panel(player)
        player.load_icon.assert_called_once()
        player.load_name_plate_image.assert_called_once()
        self.assertEqual(panel.getpixel((20, 20)), (255, 0, 0, 255))
        self.assertEqual(panel.getpixel((1000, 100)), (0, 0, 255, 255))
        self.assertIn("TITLE FROM API", [c.args[2] for c in text.call_args_list])

    def test_assets_resolve_outside_working_directory(self):
        self.assertTrue(preview.ASSETS.is_absolute())
        self.assertTrue((preview.ASSETS / "logo.png").is_file())
        self.assertEqual(preview.font(42, "song").getname()[1], "Bold")


if __name__ == "__main__":
    unittest.main()
