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


class DummyScore:
    level_index = 3
    final_level = "14.7"
    level_is_constant = True
    song_name = "SAMPLE TRACK"
    score = 1_009_786
    rank = "SSS+"
    rating_floor = 16.18

    def load_jacket_image(self):
        return Image.new("RGBA", (320, 320), (16, 177, 191, 255))


class ChunithmSingleChartRendererTests(unittest.TestCase):
    def test_push_and_fu_renderer_uses_print_sheet_canvas_and_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "push.png"
            CORE.create_push_score_image(
                DummyPlayer(), DummyScore(), path, title="随机推分",
            )
            with Image.open(path) as image:
                self.assertEqual(image.size, (CORE.PUSH_W, CORE.PUSH_H + 56))
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((10, CORE.PUSH_H + 1)), (16, 177, 191))


if __name__ == "__main__":
    unittest.main()
