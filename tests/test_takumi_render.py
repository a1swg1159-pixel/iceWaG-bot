import importlib.util
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from src import takumi_render as art
from src.takumi_collections import PlayerCollections

SPEC = importlib.util.spec_from_file_location("takumi_art_test_core", Path(__file__).resolve().parents[1] / "src/plugins/takumi_b40/core.py")
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


class OfficialStyleTests(unittest.TestCase):
    def setUp(self):
        self.render = core._official_renderer()

    def test_original_five_column_geometry(self):
        self.assertEqual((art.CW, art.CH), (core.B40_COLUMN_W, core.B40_CARD_H))
        self.assertEqual(art.COLS, 5)
        self.assertEqual(art.W, 3000)
        self.assertLessEqual(art.MARGIN+5*art.CW+4*art.GAP_X, art.W)
        self.assertLessEqual(art.MARGIN+8*art.CCW+7*art.CGAP_X, art.W)

    def test_rank_visible_heights_are_equal(self):
        for name in ("S+", "S", "AAA", "AA", "A", "BBB", "BB", "B", "C", "N"):
            for height in (43, 26):
                rank = self.render.rank_image(name, height)
                bounds = rank.getchannel("A").getbbox()
                self.assertEqual(bounds[3]-bounds[1], height)
                self.assertLess(rank.width, 100 if height == 43 else 73)

    def test_cards_and_long_names_do_not_overflow(self):
        for difficulty in art.DIFFICULTIES:
            for title in ("短曲名", "Long song name 日本語歌曲标题 " * 10):
                sample = art.Sample(153, title, difficulty, 16.5, 1000000, 5, 21.2)
                boxes = []
                original = self.render.top_text

                def capture(*args, **kwargs):
                    box = original(*args, **kwargs)
                    boxes.append(box)
                    return box

                for compact, width, height in ((False, 555, 228), (True, 350, 120)):
                    boxes.clear()
                    with patch.object(self.render, "top_text", side_effect=capture):
                        tile = self.render.card(sample, 40, compact=compact)
                    self.assertEqual(tile.size, (width, height))
                    for left, top, right, bottom in boxes:
                        self.assertGreaterEqual(left, 0)
                        self.assertGreaterEqual(top, 0)
                        self.assertLessEqual(right, width)
                        self.assertLessEqual(bottom, height)

    def test_fixed_background_scale_and_private_layers(self):
        small = self.render.background(3000, 500)
        large = self.render.background(3000, 1100)
        self.assertIsNone(ImageChops.difference(small, large.crop((0,0,3000,500))).getbbox())
        small.paste("red", (0,0,3000,500))
        self.assertNotEqual(small.tobytes(), self.render.background(3000, 500).tobytes())

    def test_b40_height_follows_actual_rows(self):
        sample = core.BestScore("chart", "Test", "MASTER", 14.2, 999000, .5, 20, "S+", 153, "14")
        for count in (1, 5, 6, 30, 40):
            scores = (sample,) * count
            result = core.B40Result(scores, 20, count, count, 0, time.time())
            with tempfile.TemporaryDirectory() as directory:
                path = core.render_b40_image(result, "Player", Path(directory), "test")
                with Image.open(path) as image:
                    rows = (count+4)//5
                    cards_bottom = art.START_Y + rows*art.CH + (rows-1)*art.GAP_Y
                    self.assertEqual(image.width, 3000)
                    self.assertEqual(image.height-cards_bottom, 40+56)

    def test_all_entrypoints_use_live_collections_without_network(self):
        item = core.BestScore("chart", "Test", "MASTER", 14.2, 999000, .5, 20, "S+", 153, "14")
        equipped = PlayerCollections(icon_id="d2_15", title="Current title", title_rank=3)
        result = core.B40Result((item,), 20, 1, 1, 0, time.time(), (item,), ((153,2,5),), equipped, "Current name")
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(core.requests.sessions.Session, "request", side_effect=AssertionError("Render must be offline")), \
             patch.object(art, "player_panel", wraps=art.player_panel) as panel:
            out = Path(directory)
            core.render_b40_image(result, "old name", out, "test")
            core.render_score_list_images(result, (item,), core.parse_level_query("14"), "old name", out, "test")
            core.render_const_list_images([core.ConstEntry(153,"Test","MASTER",14.2,999000)], core.parse_const_query("14.2"), "Current name", out, "test", collections=equipped, rating=20)
        self.assertEqual(panel.call_count, 3)
        for call in panel.call_args_list:
            self.assertEqual(call.args, ("Current name", 20, equipped))
        self.assertEqual(core._render_samples(result, (item,))[0].medal, 5)


if __name__ == "__main__":
    unittest.main()
