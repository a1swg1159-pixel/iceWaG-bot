import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


CORE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "plugins"
    / "maimai_b50"
    / "core.py"
)
SPEC = importlib.util.spec_from_file_location("maimai_b50_core_test", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CORE
SPEC.loader.exec_module(CORE)


class DummyPlayer:
    name = "TEST PLAYER"
    rating = 15000
    trophy = ""

    def load_icon(self):
        return None


class DummyScore:
    id = 1
    song_type = "dx"
    level_index = 3
    song_name = "SAMPLE TRACK"
    display_level = "14+"
    level_value = 14.8
    level = "14.8"
    achievements = 100.1234
    rank = "SSS+"
    dx_rating = 321

    def load_jacket(self):
        return Image.new("RGB", (320, 320), (16, 177, 191))


class MaimaiScoreListRendererTests(unittest.TestCase):
    def test_level_score_list_renderer_is_dynamic_and_credited(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            query = CORE.parse_level_query("14+")
            paths = CORE.create_maimai_score_list_images(
                DummyPlayer(), [DummyScore(), DummyScore()], query, output, "123",
            )
            self.assertEqual(len(paths), 1)
            with Image.open(paths[0]) as image:
                expected_body_height = (
                    CORE.HEADER_H + 8 + CORE.SECTION_HEADER_H
                    + CORE.CARD_H + 40
                )
                self.assertEqual(image.size, (CORE.CANVAS_W, expected_body_height + 56))
                self.assertEqual(image.mode, "RGB")

    def test_b50_uses_production_magical_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b50.png"
            with patch.object(CORE.mai_theme, "card", wraps=CORE.mai_theme.card) as card:
                result = CORE.create_maimai_b50_image(
                    DummyPlayer(), [DummyScore()] * 35, [DummyScore()] * 15,
                    10000, 5000, path,
                )
            self.assertEqual(result, path)
            self.assertEqual(card.call_count, 50)
            with Image.open(path) as image:
                self.assertEqual(image.size, (3000, 3110))
                self.assertEqual(image.mode, "RGB")

    def test_existing_pagination_and_global_numbering_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            scores = [DummyScore()] * 51
            with patch.object(CORE.mai_theme, "score_list", wraps=CORE.mai_theme.score_list) as render:
                paths = CORE.create_maimai_score_list_images(
                    DummyPlayer(), scores, CORE.parse_level_query("14"),
                    Path(directory), "test",
                )
            self.assertEqual(len(paths), 2)
            self.assertEqual([c.kwargs["index_offset"] for c in render.call_args_list], [0, 50])
            self.assertEqual([c.args[2] for c in render.call_args_list], ["Lv. 14  1/2", "Lv. 14  2/2"])
            with Image.open(paths[0]) as first, Image.open(paths[1]) as last:
                self.assertEqual(first.width, last.width)
                self.assertEqual(last.size, (3000, 698))
                self.assertEqual(first.height - last.height, 9 * (236 + 22))

    def test_real_collections_parsed_and_fetched_once_even_on_miss(self):
        player = CORE.MaimaiPlayer({"data": {
            "name": "PLAYER", "rating": 15555,
            "icon": {"id": "23"}, "name_plate": {"id": 456},
            "trophy": {"name": "MY TITLE"}, "star": 99,
        }})
        self.assertEqual(player.trophy, "MY TITLE")
        with patch.object(CORE.ARTWORK_CACHE, "get", return_value=None) as get:
            for _ in range(2):
                self.assertIsNone(player.load_icon())
                self.assertIsNone(player.load_name_plate_image())
        self.assertEqual([c.args for c in get.call_args_list], [("icon", 23), ("plate", 456)])
        self.assertEqual(CORE.mai_theme.rating_style(player.rating), ("rainbow", 3))

    def test_invalid_collection_ids_are_not_used_as_paths(self):
        for value in (None, "../token", {"id": -1}, {}, {"id": "oops"}, 10**11):
            with self.subTest(value=value):
                self.assertIsNone(CORE._collection_id(value))

    def test_injected_offline_art_never_downloads(self):
        player = CORE.MaimaiPlayer({})
        score = CORE.MaimaiScore({"id": 7}, {})
        player.icon_image = Image.new("RGBA", (184, 184), "red")
        player.name_plate_image = Image.new("RGBA", (720, 116), "blue")
        score.jacket_image = Image.new("RGBA", (184, 184), "green")
        with patch.object(CORE.ARTWORK_CACHE, "get", side_effect=AssertionError("network")), \
             patch.object(CORE.ARTWORK_CACHE, "thumbnail", side_effect=AssertionError("network")):
            CORE.prefetch_report_assets(player, [score, score])
            self.assertEqual(score.load_jacket_thumbnail(184).size, (184, 184))
            self.assertIs(player.load_icon(), player.icon_image)
            self.assertIs(player.load_name_plate_image(), player.name_plate_image)

    def test_prefetch_schedules_actual_needed_art_once_per_object(self):
        player = CORE.MaimaiPlayer({})
        score = CORE.MaimaiScore({"id": 7}, {})
        with patch.object(CORE.DOWNLOAD_POOL, "submit", return_value=Mock()) as submit:
            CORE.prefetch_report_assets(player, [score, score])
        self.assertEqual(submit.call_count, 3)
        self.assertEqual(submit.call_args_list[-1].args, (score.load_jacket_thumbnail, 184))

    def test_score_payload_keeps_rank_badges_rating_and_constant(self):
        score = CORE.MaimaiScore({
            "id": 10234, "type": "dx", "level_index": 4, "song_name": "TEST",
            "achievements": 100.1234, "dx_rating": 319, "rate": "sss",
            "fc": "app", "fs": "fsdp", "level_value": 14.7,
        }, {})
        self.assertEqual((score.id, score.level, score.rank, score.dx_rating), (234, "14.7", "SSS", 319))
        self.assertEqual(CORE.mai_theme.badge_key(score.fc), "app")
        self.assertEqual(CORE.mai_theme.badge_key(score.fs, sync=True), "fdxp")


if __name__ == "__main__":
    unittest.main()
