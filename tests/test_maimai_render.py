import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

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
                    CORE.HEADER_H + 10 + CORE.SECTION_HEADER_H
                    + CORE.CARD_H + 24 + 24
                )
                self.assertEqual(image.size, (CORE.CANVAS_W, expected_body_height + 56))
                self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
