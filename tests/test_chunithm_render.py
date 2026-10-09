import importlib.util
import hashlib
import io
import json
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from src.chunithm_assets import ArtworkCache


CORE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "plugins"
    / "chunithm_b30"
    / "b30_core.py"
)
SPEC = importlib.util.spec_from_file_location("chunithm_b30_core_test", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CORE
SPEC.loader.exec_module(CORE)


class DummyPlayer:
    name = "TEST PLAYER"
    rating_floor = 16.42

    def load_character_image(self):
        return None

    def load_trophy_image(self):
        return None

    trophy_name = ""
    trophy_color = "normal"


class DummyScore:
    level_index = 3
    final_level = "14.7"
    level_is_constant = True
    song_name = "SAMPLE TRACK"
    score = 1_009_786
    rank = "SSS+"
    rating_floor = 16.18
    raw_level = "14+"

    def load_jacket_image(self):
        return Image.new("RGBA", (320, 320), (16, 177, 191, 255))


class ChunithmRendererTests(unittest.TestCase):
    def setUp(self):
        cache_directory = tempfile.TemporaryDirectory()
        self.addCleanup(cache_directory.cleanup)
        cache_patch = patch.object(CORE, "ARTWORK_CACHE", ArtworkCache(cache_directory.name))
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        network_patch = patch.object(CORE.requests, "get", side_effect=AssertionError("Unexpected network request"))
        network_patch.start()
        self.addCleanup(network_patch.stop)

    def test_player_reads_all_equipped_collection_ids(self):
        player = CORE.Player({"data": {
            "name": "TEST", "rating": 16.42, "level": 42, "reborn_count": 3,
            "class_emblem": {"base": 5, "medal": 4},
            "map_icon": {"id": 19}, "name_plate": {"id": 10131},
            "character": {"id": 16620},
            "trophy": {"id": 866, "name": "主称号", "color": "IMAGE"},
        }})
        self.assertEqual(player.name_plate_id, 10131)
        self.assertEqual(player.trophy_name, "主称号")
        self.assertEqual(player.level, 42)
        self.assertEqual(player.reborn_count, 3)
        self.assertEqual(player.class_emblem, {"base": 5, "medal": 4})
        with patch.object(player, "_load_collection_image", return_value=Image.new("RGBA", (32, 32))) as loader:
            player.load_name_plate_image()
            player.load_map_icon_image()
            player.load_character_image()
            player.load_trophy_image()
        self.assertEqual([c.args for c in loader.call_args_list], [
            ("plate", 10131), ("icon", 19), ("character", 16620), ("trophy", 866),
        ])

    def test_player_and_score_requests_run_concurrently_without_caching(self):
        barrier = threading.Barrier(2)

        def profile(credential):
            barrier.wait(timeout=3)
            return "profile"

        def scores(credential):
            barrier.wait(timeout=3)
            return ["score"]

        with patch.object(CORE, "get_player_info", side_effect=profile) as get_player:
            for _ in range(2):
                self.assertEqual(CORE._get_player_and_scores("Bearer test-token", scores), ("profile", ["score"]))
        self.assertEqual(get_player.call_count, 2)

    def test_production_jacket_drawing_uses_the_prepared_thumbnail(self):
        image = Image.new("RGB", (200, 200))
        score = SimpleNamespace(load_jacket_thumbnail=Mock(return_value=Image.new("RGBA", (170, 170), "red")),
                                load_jacket_image=Mock(side_effect=AssertionError("Full original should not be loaded")))
        CORE.chu_theme.draw_jacket(image, score, (10, 10, 170))
        score.load_jacket_thumbnail.assert_called_once_with(170)
        score.load_jacket_image.assert_not_called()

    def test_card_textures_have_recorded_official_provenance(self):
        theme = CORE.chu_theme
        data = json.loads((theme.ASSETS / "mate/sources.json").read_text(encoding="utf-8"))
        for record in data["card_ui"]["files"]:
            path = theme.ASSETS / "mate/ui" / record["file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
            self.assertTrue(record["source"].startswith("https://chunithm.sega.jp/"))

    def test_card_surfaces_are_cached_and_fall_back_without_assets(self):
        theme = CORE.chu_theme
        first = theme.card_surface(565, 256, 3)
        self.assertIs(first, theme.card_surface(565, 256, 3))
        self.assertEqual(first.size, (565, 256))
        self.assertEqual(first.getpixel((0, 0))[3], 0)
        with patch.object(theme, "ui_art", return_value=None):
            theme.card_surface.cache_clear()
            self.assertEqual(theme.card_surface(565, 256, 3).size, (565, 256))
        theme.card_surface.cache_clear()

    def test_very_long_titles_use_logarithmic_width_probes(self):
        theme = CORE.chu_theme
        with patch.object(theme, "text_width", wraps=theme.text_width) as measure:
            lines = theme.title_lines("非常长的曲名" * 250, theme.font(28, "song"), 340, max_lines=2)
        self.assertLess(measure.call_count, 40)
        self.assertTrue(lines[-1].endswith("..."))
        for line in lines:
            self.assertLessEqual(theme.text_width(line, theme.font(28, "song")), 340)

    def test_optional_collections_and_numeric_ids_are_supported(self):
        with patch.object(CORE.requests, "get") as get:
            missing = CORE.Player({"data": {"trophy": None, "name_plate": None, "map_icon": None}})
            self.assertIsNone(missing.load_name_plate_image())
            self.assertIsNone(missing.load_map_icon_image())
            self.assertIsNone(missing.load_trophy_image())
            get.assert_not_called()
        numeric = CORE.Player({"name_plate": 123, "map_icon": 19})
        self.assertEqual(numeric.name_plate_id, 123)
        self.assertEqual(numeric.map_icon_id, 19)

    def test_missing_or_invalid_profile_fields_do_not_invent_player_state(self):
        for data in ({}, {"level": -1, "reborn_count": True, "class_emblem": None},
                     {"level": "99", "reborn_count": "3", "class_emblem": {"base": "../x", "medal": 9}}):
            player = CORE.Player(data)
            self.assertIsNone(player.level)
            self.assertIsNone(player.reborn_count)
            self.assertEqual(player.class_emblem, {})

    def test_collection_download_caches_success_and_failure(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (32, 32), (123, 56, 210, 255)).save(buffer, format="PNG")
        payload = buffer.getvalue()
        # Ensure the tiny fixture passes the real minimum response-length check.
        payload += b"\0" * 128
        player = CORE.Player({"name_plate": 123})
        with patch.object(CORE.requests, "get", return_value=SimpleNamespace(status_code=200, content=payload)) as get:
            first = player.load_name_plate_image()
            self.assertIs(first, player.load_name_plate_image())
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.args[0], f"{CORE.ASSETS_BASE_URLS[0]}/chunithm/plate/123.png")
            self.assertNotIn("Authorization", get.call_args.kwargs["headers"])
            self.assertNotEqual(get.call_args.kwargs.get("verify"), False)
        failed = CORE.Player({"name_plate": 456})
        with patch.object(CORE.requests, "get", return_value=SimpleNamespace(status_code=404, content=b"")) as get:
            self.assertIsNone(failed.load_name_plate_image())
            self.assertIsNone(failed.load_name_plate_image())
            self.assertEqual(get.call_count, len(CORE.ASSETS_BASE_URLS))

    def test_b50_queries_refresh_scores_but_reuse_public_artwork(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (96, 96), (120, 180, 80, 255)).save(buffer, format="PNG")
        payload = buffer.getvalue()
        score_calls = 0
        profile_calls = 0
        artwork_calls = []

        def respond(url, **kwargs):
            nonlocal score_calls, profile_calls
            if url == CORE._player_api_url():
                profile_calls += 1
                data = {"name": "TEST", "rating": 16.42, "name_plate": 19, "map_icon": 20}
            elif url == CORE._player_api_url("/bests"):
                score_calls += 1
                data = {"bests": [{"id": 101, "song_name": "TRACK", "level": "14+",
                                   "level_index": 3, "rank": "sssp", "rating": 16.42,
                                   "score": 1009900 + score_calls}], "new_bests": []}
            else:
                self.assertNotIn("Authorization", kwargs["headers"])
                artwork_calls.append(url)
                return SimpleNamespace(status_code=200, content=payload)
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": data})

        catalog = {101: {"id": 101, "difficulties": [{"difficulty": 3, "level_value": 14.7}]}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(CORE, "ALL_SONGS_CACHE", catalog), \
             patch.object(CORE.requests, "get", side_effect=respond):
            first = CORE.generate_b50_image("Bearer test-token", Path(directory), "test")
            with Image.open(first) as image:
                first_pixels = image.tobytes()
            # A new cache instance models restarting the bot/container.
            CORE.ARTWORK_CACHE = ArtworkCache(CORE.ARTWORK_CACHE.root)
            second = CORE.generate_b50_image("Bearer test-token", Path(directory), "test")
            with Image.open(second) as image:
                self.assertNotEqual(first_pixels, image.tobytes())
        self.assertEqual((profile_calls, score_calls), (2, 2))
        self.assertEqual(len(artwork_calls), 3)

    def test_all_report_wrappers_prefetch_only_their_visible_scores(self):
        scores = [DummyScore() for _ in range(60)]
        player = DummyPlayer()
        theme = CORE.chu_theme
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(CORE, "prefetch_report_assets") as prefetch, \
             patch.object(theme, "render_score_sheet"), \
             patch.object(theme, "render_single_sheet"), \
             patch.object(theme, "render_fit_sheet"), \
             patch.object(theme, "save_report", return_value=Path(directory) / "test.png"):
            path = Path(directory) / "test.png"
            CORE.create_b30_style_image(player, scores, 16.42, path)
            self.assertEqual(prefetch.call_args.args[1], scores[:30])
            CORE.create_b50_style_image(player, scores[:35], scores[35:], 16.42, path)
            self.assertEqual(prefetch.call_args.args[1], scores[:30] + scores[35:55])
            CORE.create_push_score_image(player, scores[0], path)
            self.assertEqual(prefetch.call_args.args[1], [scores[0]])
            prefetch.reset_mock()
            CORE.create_chunithm_score_list_images(player, scores, CORE.parse_level_query("14+"), Path(directory), "test")
            self.assertEqual([call.args[1] for call in prefetch.call_args_list], [scores[:50], scores[50:]])
            entries = [CORE.FitConstEntry(101, "TRACK", 3, 14.7, scores[0])]
            CORE.create_chunithm_fitconst_image(player, entries, CORE.parse_constant_range_query("14"), Path(directory), "test")
            self.assertEqual(prefetch.call_args.args[1], [scores[0]])

    def test_fast_png_save_preserves_every_pixel_including_credit(self):
        theme = CORE.chu_theme
        source = theme.render_score_sheet(DummyPlayer(), [theme.ScoreSection("", [DummyScore()])], "BEST 30")
        expected = theme.append_image_credit(source, theme.font(28), footer_fill=theme.WHITE,
                                             text_fill=theme.MUTED, rule_fill=theme.YELLOW)
        with tempfile.TemporaryDirectory() as directory:
            path = theme.save_report(source, Path(directory) / "fast.png")
            with Image.open(path) as saved:
                self.assertEqual(saved.size, expected.size)
                self.assertEqual(saved.mode, expected.mode)
                self.assertEqual(saved.tobytes(), expected.tobytes())

    def test_invalid_collection_ids_do_not_become_request_paths(self):
        player = CORE.Player({})
        with patch.object(CORE.requests, "get") as get:
            for invalid in ("../x", "123", -1, True, {}, None):
                self.assertIsNone(player._load_collection_image("plate", invalid))
            self.assertIsNone(player._load_collection_image("../private", 123))
            get.assert_not_called()

    def test_header_removes_report_titles_but_keeps_range_and_player_name(self):
        theme = CORE.chu_theme
        for title in ("BEST 30", "BEST 50", "FIT CONSTANT", "SCORE LIST", "随机推分", "装福"):
            image = theme.canvas(theme.SINGLE_WIDTH, theme.HEADER_HEIGHT)
            with patch.object(theme, "text", wraps=theme.text) as draw_text, \
                 patch.object(theme, "profile_label", wraps=theme.profile_label) as profile_label:
                theme.draw_header(image, DummyPlayer(), title, label="14.0-15.9")
            values = [c.args[2] for c in draw_text.call_args_list]
            self.assertNotIn(title, values)
            self.assertIn(DummyPlayer.name, [c.args[2] for c in profile_label.call_args_list])
            self.assertIn("14.0-15.9", values)

    def test_header_has_no_duplicate_right_rating_or_b30_average(self):
        theme = CORE.chu_theme
        player = DummyPlayer()
        image = theme.canvas(theme.SINGLE_WIDTH, theme.HEADER_HEIGHT)
        with patch.object(theme, "text", wraps=theme.text) as draw_text, \
             patch.object(theme, "_profile_rating", wraps=theme._profile_rating) as profile_rating:
            theme.draw_header(image, player, "BEST 30", rating=19.99)
        profile_rating.assert_called_once()
        self.assertEqual(profile_rating.call_args.args[1], player.rating_floor)
        values = [c.args[2] for c in draw_text.call_args_list]
        self.assertNotIn("RATING", values)
        self.assertNotIn("19.99", values)
        # The enlarged MATE logo now occupies the former right-rating region.

    def test_profile_prefers_map_avatar_and_draws_real_plate_and_image_trophy(self):
        theme = CORE.chu_theme
        player = SimpleNamespace(
            name="PLAYER", trophy_name="DO NOT DRAW OVER IMAGE", trophy_color="image",
            load_map_icon_image=Mock(return_value=Image.new("RGBA", (164, 184), (220, 10, 20, 255))),
            load_character_image=Mock(return_value=Image.new("RGBA", (100, 300), (5, 5, 5, 255))),
            load_name_plate_image=Mock(return_value=Image.new("RGBA", (600, 238), (10, 180, 30, 255))),
            load_trophy_image=Mock(return_value=Image.new("RGBA", (560, 58), (10, 20, 220, 255))),
        )
        image = theme.canvas(theme.SINGLE_WIDTH, theme.HEADER_HEIGHT)
        with patch.object(theme, "text", wraps=theme.text) as draw_text:
            theme.draw_profile(image, player)
        player.load_character_image.assert_not_called()
        scale = theme.PROFILE_PLATE_WIDTH / 576
        def sample(x, y):
            return image.getpixel((theme.MARGIN + round(x*scale), theme.PROFILE_TOP + round(y*scale)))
        self.assertEqual(sample(520, 140), (220, 10, 20))  # Portrait on the right.
        self.assertEqual(sample(40, 125), (10, 180, 30))  # Plate art visible on left.
        self.assertEqual(sample(300, 74), (10, 20, 220))  # Trophy above name.
        self.assertNotIn(player.trophy_name, [c.args[2] for c in draw_text.call_args_list])

    def test_profile_uses_character_fallback_and_truncates_long_trophy(self):
        theme = CORE.chu_theme
        player = SimpleNamespace(
            name="很长的玩家昵称" * 50, trophy_name="主称号" * 60, trophy_color="gold",
            load_map_icon_image=lambda: None,
            load_character_image=Mock(return_value=Image.new("RGBA", (60, 180), (80, 120, 200, 255))),
            load_trophy_image=lambda: None,
            load_name_plate_image=lambda: None,
        )
        with patch.object(theme.ImageDraw.ImageDraw, "text", autospec=True) as draw_text:
            theme.draw_profile(theme.canvas(theme.SINGLE_WIDTH, theme.HEADER_HEIGHT), player)
        player.load_character_image.assert_called_once()
        for call in draw_text.call_args_list:
            value = call.args[2]
            self.assertTrue(value.endswith("..."))
            self.assertLessEqual(theme.text_width(value, call.kwargs["font"]), 800)

    def test_profile_preserves_collection_transparency_and_missing_avatar_geometry(self):
        theme = CORE.chu_theme
        sprite = Image.new("RGBA", (576, 228))
        sprite.paste((20, 180, 30, 255), (100, 10, 200, 30))
        player = SimpleNamespace(name="NAME", load_name_plate_image=lambda: sprite)
        self.assertEqual(theme.collection_image(player, "load_name_plate_image", trim=False).size, sprite.size)
        profile = theme.render_profile(player)
        self.assertEqual(profile.size, (theme.PROFILE_PLATE_WIDTH, theme.PROFILE_PLATE_HEIGHT))
        scale = theme.PROFILE_PLATE_WIDTH / 576
        self.assertEqual(profile.getpixel((round(120*scale), round(20*scale)))[:3], (20, 180, 30))
        self.assertEqual(profile.getpixel((20, 20))[3], 0)
        with patch.object(theme, "profile_label", wraps=theme.profile_label) as label:
            theme.render_profile(player)
        self.assertNotIn("Lv.", [c.args[2] for c in label.call_args_list])

    def test_profile_rating_assets_and_tiers_are_complete(self):
        theme = CORE.chu_theme
        for value, tier in ((0, "green"), (4, "orange"), (7, "red"), (10, "purple"),
                            (12, "bronze"), (13.25, "silver"), (14.5, "gold"),
                            (15.25, "platinum"), (16, "rainbow"), (17, "kiwami")):
            self.assertEqual(theme.profile_rating_tier(value), tier)
            sprite = theme.profile_rating_map(tier)
            self.assertIsNotNone(sprite)
            self.assertEqual(sprite.mode, "RGBA")
            self.assertEqual(sprite.width % 4, 0)
            self.assertEqual(sprite.height % 4, 0)
        for kind in ("base", "medal"):
            for index in range(1, 7):
                self.assertIsNotNone(theme.profile_asset(f"profile/class_{kind}/{index}.webp"))

    def test_profile_fallback_and_invalid_ratings_do_not_crash(self):
        theme = CORE.chu_theme
        with patch.object(theme, "profile_rating_map", return_value=None):
            theme.render_profile(DummyPlayer())
        for value in (None, float("nan"), float("inf"), -1, "17.42"):
            image = Image.new("RGBA", (1152, 456))
            theme._profile_rating(image, value)
            self.assertIsNone(image.getbbox())

    def test_profile_uses_full_cjk_font_for_missing_simplified_characters(self):
        theme = CORE.chu_theme
        self.assertTrue(theme.profile_regular_supports("CHUNITHM Mate"))
        self.assertFalse(theme.profile_regular_supports("冰哥测试"))
        image = Image.new("RGBA", (1152, 456))
        with patch.object(theme.ImageDraw.ImageDraw, "text", autospec=True) as draw_text:
            theme.profile_label(image, (240, 135), "冰哥测试", 39, 221, full_width=True)
        face = draw_text.call_args.kwargs["font"]
        self.assertIn("Noto Sans CJK", face.getname()[0])
        unknown = bytes(face.getmask("\U0010ffff"))
        for char in "冰哥测试":
            self.assertNotEqual(bytes(face.getmask(char)), unknown)

    def test_profile_nickname_always_uses_original_bold_face(self):
        theme = CORE.chu_theme
        for name in ("iceWaG", "冰哥测试", "星咲あかり"):
            player = SimpleNamespace(name=name, trophy_name="CHUNITHM Mate")
            with patch.object(theme, "profile_label", wraps=theme.profile_label) as label:
                theme.render_profile(player)
            nickname = next(call for call in label.call_args_list if call.args[2] == name)
            self.assertTrue(nickname.kwargs["bold"])
            self.assertLessEqual(nickname.args[1][0] + nickname.args[4], theme.PROFILE_AVATAR_LEFT - 8)
            trophy = next(call for call in label.call_args_list if call.args[2] == player.trophy_name)
            self.assertTrue(trophy.kwargs["bold"])

    def test_profile_frames_do_not_spill_into_plate_alpha_padding(self):
        theme = CORE.chu_theme
        # Real 576x228 plates may only paint 561x202, leaving right/bottom padding.
        plate = Image.new("RGBA", (576, 228))
        plate.paste((40, 110, 160, 255), (0, 0, 561, 202))
        player = SimpleNamespace(
            name="iceWaG", level=92, reborn_count=6, rating_floor=17.31,
            trophy_name="CHUNITHM Mate", class_emblem={"base": 5, "medal": 5},
            load_name_plate_image=lambda: plate,
            load_map_icon_image=lambda: Image.new("RGBA", (256, 256), "red"),
        )
        image = theme.render_profile(player)
        base = Image.new("RGBA", (1152, 456))
        theme._profile_paste(base, plate, (0, 0, 576, 228), contain=True)
        base = base.resize(image.size, theme.LANCZOS)
        scale = theme.PROFILE_PLATE_WIDTH / 576
        # Leave four native pixels for the resampling kernel, then require
        # unchanged pixels, including the nameplate's original transparent edge.
        for region in (
            (round((theme.PROFILE_RIGHT+4)*scale), 0, image.width, image.height),
            (round(theme.PROFILE_LEFT*scale), round((theme.PROFILE_INFO_BOTTOM+4)*scale),
             image.width, image.height),
        ):
            self.assertEqual(image.crop(region).tobytes(), base.crop(region).tobytes())

    def test_profile_trophies_share_the_inset_right_boundary(self):
        theme = CORE.chu_theme
        for trophy in (None, Image.new("RGBA", (1200, 80), "blue")):
            for title in ("CHUNITHM Mate", "主称号" * 60):
                image = Image.new("RGBA", (1152, 456))
                player = SimpleNamespace(trophy_name=title, load_trophy_image=lambda: trophy)
                theme._profile_trophy(image, player)
                left, top, right, bottom = image.getbbox()
                self.assertGreaterEqual(left, theme.PROFILE_LEFT*2)
                self.assertLessEqual(right, theme.PROFILE_RIGHT*2)
                self.assertGreaterEqual(top, 54*2)
                self.assertLessEqual(bottom, 94*2)

    def test_profile_portrait_and_emblems_fit_inside_shared_frame(self):
        theme = CORE.chu_theme
        player = SimpleNamespace(
            name="NAME", level=99, reborn_count=10, rating_floor=17.31,
            class_emblem={"base": 6, "medal": 6},
            load_map_icon_image=lambda: Image.new("RGBA", (200, 1000), "red"),
        )
        with patch.object(theme, "_profile_paste", wraps=theme._profile_paste) as paste:
            theme.render_profile(player)
        for call in paste.call_args_list:
            x, y, width, height = call.args[2]
            self.assertGreaterEqual(x, theme.PROFILE_LEFT)
            self.assertGreaterEqual(y, theme.PROFILE_INFO_TOP)
            self.assertLessEqual(x+width, theme.PROFILE_RIGHT)
            self.assertLessEqual(y+height, theme.PROFILE_INFO_BOTTOM)

    def test_header_falls_back_without_logo_and_accepts_profile_art(self):
        theme = CORE.chu_theme
        player = SimpleNamespace(
            name="很长的玩家昵称 Player " * 20,
            rating_floor=16.42,
            load_character_image=lambda: Image.new("RGBA", (256, 400), (80, 120, 200, 255)),
            load_trophy_image=lambda: Image.new("RGBA", (640, 80), (180, 140, 220, 255)),
        )
        image = theme.canvas(theme.FIT_WIDTH, theme.FIT_HEADER_HEIGHT)
        with patch.object(theme, "logo", return_value=None):
            theme.draw_header(image, player, "FIT CONSTANT", label="14.0-15.9")
        self.assertEqual(image.size, (theme.FIT_WIDTH, theme.FIT_HEADER_HEIGHT))

    def test_empty_score_list_does_not_make_blank_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(CORE.create_chunithm_score_list_images(
                DummyPlayer(), [], CORE.parse_level_query("14+"), Path(directory), "123",
            ), [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_oversized_fit_sheet_is_rejected_before_allocating_image(self):
        entry = CORE.FitConstEntry(101, "SAMPLE TRACK", 3, 14.2, DummyScore())
        entries = [entry] * (CORE.chu_theme.FIT_COLUMNS * (CORE.FIT_MAX_HEIGHT // CORE.chu_theme.FIT_CARD_HEIGHT + 1))
        with patch.object(CORE.chu_theme, "render_fit_sheet") as render:
            with self.assertRaisesRegex(ValueError, "缩小定数范围"):
                CORE.create_chunithm_fitconst_image(
                    DummyPlayer(), entries, CORE.parse_constant_range_query("14"),
                    Path("unused-output"), "123",
                )
        render.assert_not_called()

    def test_score_fetch_keeps_rows_without_rating(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{
                "id": 101, "song_name": "SAMPLE TRACK", "level_index": 3,
                "level": "14+", "score": 1_009_000, "rank": "sssp",
            }]},
        )
        with patch.object(CORE, "ALL_SONGS_CACHE", {
            101: {"id": 101, "difficulties": [
                {"difficulty": 3, "level_value": 14.2},
            ]},
        }), patch.object(CORE.requests, "get", return_value=response, create=True):
            scores = CORE.get_all_player_scores("Bearer test-token")
        self.assertEqual(len(scores), 1)
        self.assertEqual(scores[0].score, 1_009_000)

    def test_fit_curve_is_bounded_and_monotone(self):
        values = [
            CORE.fit_chunithm_constant(14.4, score)
            for score in (975000, 1000000, 1007000, 1008500, 1009500, 1010000)
        ]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(values[3], 14.4)
        self.assertGreater(values[0], 14.4)
        self.assertLess(values[-1], 14.4)

    def test_fitconst_renders_only_played_without_tenth_sections(self):
        catalog = [{
            "id": 101, "title": "SAMPLE TRACK",
            "difficulties": [
                {"difficulty": 3, "level_value": 14.2},
                {"difficulty": 4, "level_value": 14.3},
            ],
        }]
        play = DummyScore()
        play.id = 101
        play.origin_id = 101
        play.level_index = 3
        query = CORE.parse_constant_range_query("14")
        entries = CORE.build_fitconst_entries(catalog, [play], query)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].constant, 14.2)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                DummyScore, "load_jacket_image",
                return_value=Image.new("RGBA", (320, 320), (16, 177, 191, 255)),
            ):
                path = CORE.create_chunithm_fitconst_image(
                    DummyPlayer(), entries, query, Path(directory), "123",
                )
            with Image.open(path) as image:
                self.assertEqual(image.width, CORE.FIT_W)
                self.assertEqual(image.height, CORE.chu_theme.fit_sheet_height(1) + 56)
                self.assertEqual(image.mode, "RGB")

    def test_push_and_fu_renderer_uses_mate_canvas_and_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "push.png"
            CORE.create_push_score_image(
                DummyPlayer(), DummyScore(), path, title="随机推分",
            )
            with Image.open(path) as image:
                self.assertEqual(image.size, (CORE.PUSH_W, CORE.PUSH_H + 56))
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((10, CORE.PUSH_H + 1)), CORE.chu_theme.YELLOW)

    def test_b30_and_b50_renderers_keep_full_canvas_and_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            b30 = CORE.create_b30_style_image(
                DummyPlayer(), [DummyScore()] * 30, 16.42, output / "b30.png",
            )
            b50 = CORE.create_b50_style_image(
                DummyPlayer(), [DummyScore()] * 30, [DummyScore()] * 20, 16.42,
                output / "b50.png",
            )
            theme = CORE.chu_theme
            with Image.open(b30) as image:
                self.assertEqual(image.width, theme.SHEET_WIDTH)
                last_y = theme.BEST_HEADER_HEIGHT + (29 // theme.COLUMNS) * (theme.CARD_HEIGHT + theme.GAP)
                self.assertEqual(image.getpixel((theme.MARGIN + theme.CARD_JACKET_X + 10,
                                                last_y + theme.CARD_JACKET_Y + 10)), (16, 177, 191))
                self.assertLess(last_y + theme.CARD_HEIGHT, image.height - 56)
            with Image.open(b50) as image:
                old_height = theme.section_height(theme.ScoreSection("OLD BEST 30", [DummyScore()] * 30))
                last_y = theme.BEST_HEADER_HEIGHT + old_height + theme.SECTION_HEIGHT + (19 // theme.COLUMNS) * (theme.CARD_HEIGHT + theme.GAP)
                self.assertEqual(image.width, theme.SHEET_WIDTH)
                self.assertEqual(image.getpixel((theme.MARGIN + theme.CARD_JACKET_X + 10,
                                                last_y + theme.CARD_JACKET_Y + 10)), (16, 177, 191))
                self.assertLess(last_y + theme.CARD_HEIGHT, image.height - 56)

    def test_both_list_types_wrap_after_five_cards_at_fixed_width(self):
        theme = CORE.chu_theme
        self.assertEqual(theme.CARD_WIDTH, 565)
        self.assertEqual(theme.FIT_CARD_WIDTH, 850)
        self.assertEqual(theme.CARD_HEIGHT, 256)
        self.assertEqual(theme.FIT_CARD_HEIGHT, 236)
        scores = [DummyScore()] * 6
        entries = [CORE.FitConstEntry(101, "SAMPLE TRACK", 3, 14.2, s) for s in scores]
        for renderer, draw_name, expected_width, card_width, args in (
            (theme.render_score_sheet, "draw_score_card", 3009, 565,
             (DummyPlayer(), [theme.ScoreSection("", scores)], "BEST 30")),
            (theme.render_fit_sheet, "draw_fit_card", 4434, 850,
             (DummyPlayer(), entries, "14.0-14.9")),
        ):
            with self.subTest(renderer=renderer.__name__):
                with patch.object(theme, draw_name) as draw_card:
                    image = renderer(*args)
                self.assertEqual(image.width, expected_width)
                positions = [call.args[2:4] for call in draw_card.call_args_list]
                self.assertEqual(len(set(y for _, y in positions[:5])), 1)
                self.assertEqual(len(set(x for x, _ in positions[:5])), 5)
                self.assertEqual(positions[5][0], positions[0][0])
                self.assertGreater(positions[5][1], positions[0][1])
                self.assertEqual(positions[4][0] + card_width, image.width - theme.MARGIN)

    def test_mate_background_has_an_offline_fallback(self):
        theme = CORE.chu_theme
        with patch.object(theme, "background_tile", return_value=None):
            image = theme.canvas(1800, 1200)
        self.assertEqual(image.getpixel((100, 100)), theme.WHITE)
        self.assertEqual(image.getpixel((900, 900)), theme.PAPER)

    def test_best_header_places_art_between_profile_and_larger_logo(self):
        theme = CORE.chu_theme
        image = theme.canvas(theme.SHEET_WIDTH, theme.BEST_HEADER_HEIGHT,
                             header_height=theme.BEST_HEADER_HEIGHT)
        with patch.object(theme, "best_header_art", wraps=theme.best_header_art) as art, \
             patch.object(theme, "draw_wordmark", wraps=theme.draw_wordmark) as wordmark:
            theme.draw_header(image, DummyPlayer(), "BEST 50")
        left = theme.MARGIN + theme.PROFILE_PLATE_WIDTH + 28
        right = theme.SHEET_WIDTH - theme.MARGIN - theme.BEST_LOGO_SIZE[0] - 28
        art.assert_called_once_with(right-left)
        self.assertEqual(wordmark.call_args.kwargs["size"], (550, 250))
        banner = theme.best_header_art(right-left)
        self.assertEqual(banner.size, (right-left, theme.BEST_HEADER_HEIGHT))
        self.assertEqual(banner.getpixel((0, 100))[3], 0)
        self.assertEqual(banner.getpixel((banner.width-1, 100))[3], 0)

    def test_best_profile_visible_bottom_meets_first_content_row(self):
        theme = CORE.chu_theme
        profile = Image.new("RGBA", (660, 261))
        profile.paste((200, 30, 40, 255), (0, 0, 640, 240))
        image = Image.new("RGBA", (theme.SHEET_WIDTH, 400))
        with patch.object(theme, "render_profile", return_value=profile):
            theme.draw_profile(image, DummyPlayer(), bottom=theme.BEST_HEADER_HEIGHT)
        self.assertEqual(image.getbbox()[3], theme.BEST_HEADER_HEIGHT)
        for title in ("BEST 30", "BEST 50"):
            sections = [theme.ScoreSection("", [DummyScore()])]
            with patch.object(theme, "draw_score_card") as card:
                rendered = theme.render_score_sheet(DummyPlayer(), sections, title)
            self.assertEqual(card.call_args.args[3], theme.BEST_HEADER_HEIGHT)
            self.assertEqual(rendered.height, theme.score_sheet_height(sections, best=True))

    def test_character_banner_is_best_only_and_falls_back_offline(self):
        theme = CORE.chu_theme
        image = theme.canvas(theme.SHEET_WIDTH, theme.HEADER_HEIGHT)
        with patch.object(theme, "best_header_art") as art:
            for title in ("SCORE LIST", "FIT CONSTANT", "随机推分", "装福"):
                theme.draw_header(image, DummyPlayer(), title)
            art.assert_not_called()
        with tempfile.TemporaryDirectory() as directory, patch.object(theme, "ASSETS", Path(directory)):
            theme.best_header_art.cache_clear()
            try:
                self.assertIsNone(theme.best_header_art(1655))
                with patch.object(theme, "logo", return_value=None):
                    theme.draw_header(image, DummyPlayer(), "BEST 50")
            finally:
                theme.best_header_art.cache_clear()

    def test_best_character_assets_match_recorded_official_downloads(self):
        theme = CORE.chu_theme
        data = json.loads((theme.ASSETS / "mate/sources.json").read_text(encoding="utf-8"))
        for item in data["header_characters"]["files"]:
            path = theme.ASSETS / "mate/header" / item["file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
            self.assertTrue(item["source"].startswith("https://chunithm.sega.jp/storage/chara/chunithm-mate/"))
            with Image.open(path) as image:
                self.assertEqual(image.mode, "RGBA")

    def test_result_rank_loader_preserves_aspect_and_bounds(self):
        theme = CORE.chu_theme
        self.assertEqual(len(theme.RANK_ASSETS), 14)
        self.assertEqual(theme.RANK_ASSETS["SSS"], "rank_sss.webp")
        self.assertEqual(theme.RANK_ASSETS["SSS+"], "rank_sssp.webp")
        theme.rank_sprite.cache_clear()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(theme, "ASSETS", Path(directory)):
                assets = Path(directory) / "mate" / "result_ranks"
                assets.mkdir(parents=True)
                # Synthetic rectangular fixture, not game artwork.
                Image.new("RGBA", (300, 100), (200, 40, 120, 255)).save(assets / "rank_sss.webp", lossless=True)
                for size in (theme.RANK_HEIGHT, theme.SINGLE_RANK_HEIGHT):
                    sprite = theme.rank_sprite("SSS", size)
                    self.assertIsNotNone(sprite)
                    self.assertEqual(sprite.mode, "RGBA")
                    self.assertLessEqual(abs(sprite.height - sprite.width / 3), .5)
                    self.assertEqual(sprite.width, size * 3)
                    self.assertEqual(sprite.height, size)
                    self.assertEqual(theme.rank_width("SSS", size), sprite.width)
                self.assertIsNone(theme.rank_sprite("D"))
        finally:
            theme.rank_sprite.cache_clear()
        self.assertEqual(theme.normalize_rank("sssp"), "SSS+")
        self.assertEqual(theme.normalize_rank(" sp "), "S+")
        self.assertEqual(theme.normalize_rank("PENDING"), "PENDING")

    def test_bundled_mate_result_ranks_are_transparent_and_fit_all_card_sizes(self):
        theme = CORE.chu_theme
        for rank, filename in theme.RANK_ASSETS.items():
            with self.subTest(rank=rank):
                with Image.open(theme.ASSETS / "mate" / "result_ranks" / filename) as original:
                    self.assertEqual(original.size, (540, 180))
                    self.assertEqual(original.mode, "RGBA")
                    self.assertEqual(original.getpixel((0, 0))[3], 0)
                    left, top, right, bottom = original.getchannel("A").getbbox()
                    ratio = (right - left) / (bottom - top)
                for size in (theme.RANK_HEIGHT, theme.SINGLE_RANK_HEIGHT):
                    sprite = theme.rank_sprite(rank, size)
                    self.assertIsNotNone(sprite)
                    self.assertEqual(sprite.height, size)
                    bounds = sprite.getchannel("A").getbbox()
                    self.assertEqual(bounds[3] - bounds[1], size)
                    self.assertLessEqual(abs(sprite.width - sprite.height * ratio), .5)

    def test_every_rank_has_the_same_ink_height_and_top_alignment(self):
        theme = CORE.chu_theme
        for size in (theme.RANK_HEIGHT, theme.SINGLE_RANK_HEIGHT):
            for rank in theme.RANK_ASSETS:
                with self.subTest(size=size, rank=rank):
                    image = Image.new("RGBA", (400, 140))
                    theme.draw_rank(image, 370, 20, rank, size=size)
                    bounds = image.getchannel("A").getbbox()
                    self.assertEqual(bounds[1], 20)
                    self.assertEqual(bounds[3], 20 + size)

    def test_fallback_rank_text_also_uses_the_fixed_height(self):
        theme = CORE.chu_theme
        with patch.object(theme, "rank_sprite", return_value=None):
            for rank in theme.RANK_ASSETS:
                image = Image.new("RGBA", (400, 140))
                theme.draw_rank(image, 370, 20, rank)
                bounds = image.getchannel("A").getbbox()
                self.assertEqual(bounds[1], 20)
                self.assertEqual(bounds[3], 20 + theme.RANK_HEIGHT)

    def test_bundled_fonts_are_used_for_each_typographic_role(self):
        theme = CORE.chu_theme
        for kind, family in (
            ("display", "Outfit"), ("ui", "Outfit"),
            ("number", "Rajdhani"), ("song", "Noto Sans CJK JP"),
            ("cjk", "Noto Sans CJK JP"),
        ):
            self.assertEqual(theme.font(34, kind).getname()[0], family)
        face = theme.font(34, "song")
        missing = bytes(face.getmask("\u0378"))
        for character in "随机推分装福拟合定数祈神祖共歩終焉é∀":
            self.assertNotEqual(bytes(face.getmask(character)), missing, character)

    def test_missing_theme_fonts_use_the_existing_offline_fallback(self):
        theme = CORE.chu_theme
        theme.font.cache_clear()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(theme, "ASSETS", Path(directory)):
                for kind in theme.FONT_FILES:
                    self.assertGreater(theme.text_width("123", theme.font(32, kind)), 0)
        finally:
            theme.font.cache_clear()

    def test_font_downloads_have_matching_provenance_and_licenses(self):
        theme = CORE.chu_theme
        manifest = json.loads((theme.ASSETS / "mate/sources.json").read_text(encoding="utf-8"))
        root = theme.ASSETS / "mate" / manifest["typography"]["directory"]
        recorded_files = set()
        for family in manifest["typography"]["families"]:
            self.assertIn("SIL OPEN FONT LICENSE", (root / family["license_file"]).read_text(encoding="utf-8"))
            for record in family["files"]:
                path = root / record["file"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
                recorded_files.add(record["file"])
        self.assertEqual(recorded_files, set(theme.FONT_FILES.values()))

    def test_fit_and_single_score_rows_have_room_for_all_ranks(self):
        theme = CORE.chu_theme
        for rank in theme.RANK_ASSETS:
            play = DummyScore()
            play.rank = rank
            entry = CORE.FitConstEntry(101, "SAMPLE TRACK", 3, 14.2, play)
            cases = (
                (theme.draw_fit_card, (entry, 0, 0), theme.FIT_WIDTH, theme.FIT_CARD_HEIGHT),
                (theme.render_single_sheet, (DummyPlayer(), play, "随机推分"), None, None),
            )
            for renderer, args, width, height in cases:
                with self.subTest(rank=rank, renderer=renderer.__name__):
                    with patch.object(theme, "text", wraps=theme.text) as draw_text, patch.object(
                        theme, "draw_rank", wraps=theme.draw_rank,
                    ) as draw_rank:
                        if width is None:
                            renderer(*args)
                        else:
                            renderer(Image.new("RGB", (width, height)), *args)
                    score_call = next(c for c in draw_text.call_args_list if c.args[2] == "1,009,786")
                    xy, number, size = score_call.args[1:4]
                    number_right = xy[0] + theme.text_width(number, theme.font(size, "number"))
                    rank_call = draw_rank.call_args
                    rank_right = rank_call.args[1]
                    rank_size = rank_call.kwargs.get("size", theme.RANK_HEIGHT)
                    self.assertLessEqual(number_right + 12, rank_right - theme.rank_width(rank, rank_size))
                    if width is not None:
                        self.assertLess(rank_right, theme.FIT_CARD_WIDTH - 280)

    def test_grade_changes_do_not_resize_or_overlap_score_digits(self):
        theme = CORE.chu_theme
        sizes = set()
        for rank in theme.RANK_ASSETS:
            score = DummyScore()
            score.rank = rank
            image = Image.new("RGB", (theme.CARD_WIDTH, theme.CARD_HEIGHT), theme.WHITE)
            with patch.object(theme, "text", wraps=theme.text) as draw_text:
                theme.draw_score_card(image, score, 0, 0, 0)
            number_call = next(c for c in draw_text.call_args_list if c.args[2] == "1,009,786")
            xy, number, size = number_call.args[1:4]
            sizes.add(size)
            number_right = xy[0] + theme.text_width(number, theme.font(size, "number"))
            rank_left = theme.CARD_WIDTH - 18 - theme.rank_width(rank)
            self.assertLessEqual(number_right + 12, rank_left)
        self.assertEqual(len(sizes), 1)

    def test_rank_renderer_pastes_the_asset_without_a_badge_background(self):
        theme = CORE.chu_theme
        sprite = theme.rank_sprite("SSS+", theme.SINGLE_RANK_HEIGHT)
        image = Image.new("RGB", (300, 120), theme.WHITE)
        left = theme.draw_rank(image, 280, 10, "SSS+", size=theme.SINGLE_RANK_HEIGHT)
        expected = Image.new("RGB", image.size, theme.WHITE)
        expected.paste(sprite, (left, 10), sprite)
        self.assertEqual(image.tobytes(), expected.tobytes())

    def test_corrupt_result_asset_falls_back_without_crashing(self):
        theme = CORE.chu_theme
        theme.rank_sprite.cache_clear()
        try:
            with patch.object(theme.Image, "open", side_effect=OSError("corrupt asset")):
                self.assertIsNone(theme.rank_sprite("SSS"))
                image = Image.new("RGB", (200, 80), theme.WHITE)
                theme.draw_rank(image, 180, 10, "SSS")
        finally:
            theme.rank_sprite.cache_clear()

    def test_missing_or_unknown_rank_uses_unboxed_text(self):
        theme = CORE.chu_theme
        self.assertIsNone(theme.rank_sprite("?"))
        with patch.object(theme, "rank_sprite", return_value=None):
            image = Image.new("RGB", (200, 80), theme.WHITE)
            left = theme.draw_rank(image, 180, 10, "SSS+")
            self.assertEqual(left, 180 - theme.rank_width("SSS+"))
            self.assertEqual(image.getpixel((left, 10)), theme.WHITE)

    def test_panels_have_no_hard_shadow_or_yellow_data_strip(self):
        theme = CORE.chu_theme
        image = Image.new("RGB", (theme.CARD_WIDTH + 20, theme.CARD_HEIGHT + 20), theme.PAPER)
        theme.draw_score_card(image, DummyScore(), 0, 0, 0)
        self.assertEqual(image.getpixel((theme.CARD_WIDTH + 3, 100)), theme.PAPER)
        background = theme.jacket_card(DummyScore(), theme.SCORE_CARD_LAYOUT).convert("RGB")
        self.assertEqual(image.getpixel((250, 230)), background.getpixel((250, 230)))
        self.assertNotIn(image.getpixel((250, 230)), (theme.WHITE, theme.YELLOW))

    def test_outlined_difficulty_marker_and_letters_have_identical_vertical_bounds(self):
        theme = CORE.chu_theme
        for level in theme.DIFFICULTIES:
            for size in (23, 25, 35):
                letters, marker, stroke = theme.difficulty_label_masks(level, size)
                self.assertEqual(letters.getbbox()[1::2], marker.getbbox()[1::2])
                sprite = theme.difficulty_label_sprite(level, size)
                bounds = sprite.getchannel("A").getbbox()
                self.assertEqual(bounds[1], letters.getbbox()[1] - stroke)
                self.assertEqual(bounds[3], letters.getbbox()[3] + stroke)
                colors = {color for _, color in sprite.getcolors(sprite.width * sprite.height)}
                self.assertIn((*theme.DIFFICULTY_INK[level], 255), colors)
                self.assertIn((255, 247, 230, 255), colors)
        self.assertEqual(theme.DIFFICULTY_INK[4], (21, 20, 25))

    def test_title_size_adapts_only_within_readable_bounds(self):
        theme = CORE.chu_theme
        lines, size = theme.adaptive_card_title("Forsaken Tale", 340)
        self.assertEqual((lines, size), (("Forsaken Tale",), 38))
        for title in ("Crossmythos Rhapsodia", "非常长的曲名" * 200, "", "祈 -我ら神祖と共に歩む者なり-"):
            lines, size = theme.adaptive_card_title(title, 340)
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(28 <= size <= 38)
            self.assertTrue(all(theme.text_width(line, theme.font(size, "song")) <= 340 for line in lines))
        lines, size = theme.adaptive_card_title("非常长的曲名" * 200, 340)
        self.assertEqual(size, 28)
        self.assertTrue(lines[-1].endswith("..."))

    def test_new_cards_use_one_prepared_thumbnail_without_loading_original(self):
        theme = CORE.chu_theme
        score = DummyScore()
        score.load_jacket_thumbnail = Mock(return_value=Image.new("RGBA", (170, 170), "red"))
        score.load_jacket_image = Mock(side_effect=AssertionError("Unexpected full-size load"))
        image = Image.new("RGB", (theme.CARD_WIDTH, theme.CARD_HEIGHT))
        theme.draw_score_card(image, score, 0, 0, 0)
        score.load_jacket_thumbnail.assert_called_once_with(170)
        score.load_jacket_image.assert_not_called()

    def test_cached_backgrounds_never_retain_a_players_score_text(self):
        theme = CORE.chu_theme
        score = DummyScore()
        first = Image.new("RGB", (theme.CARD_WIDTH, theme.CARD_HEIGHT))
        theme.draw_score_card(first, score, 0, 0, 0)
        clean = theme.jacket_card(score, theme.SCORE_CARD_LAYOUT).tobytes()
        score.score = 777777
        second = Image.new("RGB", first.size)
        theme.draw_score_card(second, score, 0, 0, 0)
        self.assertNotEqual(first.tobytes(), second.tobytes())
        self.assertEqual(clean, theme.jacket_card(score, theme.SCORE_CARD_LAYOUT).tobytes())

    def test_missing_cover_uses_a_dark_local_background_without_crashing(self):
        theme = CORE.chu_theme
        score = DummyScore()
        score.load_jacket_image = Mock(side_effect=OSError("missing image"))
        image = Image.new("RGB", (theme.CARD_WIDTH + 8, theme.CARD_HEIGHT + 8), theme.PAPER)
        theme.draw_score_card(image, score, 0, 0, 0)
        self.assertLess(max(image.getpixel((400, 130))), 80)
        self.assertEqual(image.getpixel((theme.CARD_WIDTH + 2, 100)), theme.PAPER)

    def test_missing_rank_asset_uses_light_fallback_on_dark_cards(self):
        theme = CORE.chu_theme
        with patch.object(theme, "rank_sprite", return_value=None), \
             patch.object(theme, "rank_text_sprite", wraps=theme.rank_text_sprite) as fallback:
            theme.draw_score_card(Image.new("RGB", (565, 256)), DummyScore(), 0, 0, 0)
        fallback.assert_any_call("SSS+", theme.RANK_HEIGHT, theme.CARD_INK)

    def test_fit_card_preserves_long_title_at_readable_size(self):
        theme = CORE.chu_theme
        title = "The Metaverse -First story of the SeelischTact-"
        size = 34
        width = theme.FIT_CARD_WIDTH - 252
        lines = theme.card_title(title, width, size)
        self.assertLessEqual(len(lines), 2)
        self.assertNotIn("...", "".join(lines))
        self.assertEqual("".join(lines).replace(" ", ""), title.replace(" ", ""))
        for line in lines:
            self.assertLessEqual(theme.text_width(line, theme.font(size, "song")), width)

    def test_difficulties_and_grade_variants_fit_the_card(self):
        theme = CORE.chu_theme
        for level, grade in enumerate(("D", "S", "SS", "SSS", "SSS+", "AAA")):
            with self.subTest(level=level, grade=grade):
                play = DummyScore()
                play.level_index = level
                play.rank = grade
                play.song_name = "非常长的曲名" * 20
                image = theme.canvas(theme.CARD_WIDTH + 24, theme.CARD_HEIGHT + 24)
                theme.draw_score_card(image, play, 0, 0, 0)
                self.assertLessEqual(theme.rank_width(grade), theme.CARD_WIDTH - 288)

    def test_level_score_list_renderer_is_dynamic_and_credited(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            query = CORE.parse_level_query("14+")
            paths = CORE.create_chunithm_score_list_images(
                DummyPlayer(), [DummyScore(), DummyScore()], query, output, "123",
            )
            self.assertEqual(len(paths), 1)
            with Image.open(paths[0]) as image:
                theme = CORE.chu_theme
                expected_body_height = theme.score_sheet_height([
                    theme.ScoreSection("", [DummyScore(), DummyScore()]),
                ])
                self.assertEqual(image.size, (CORE.B50_W, expected_body_height + 56))
                self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
