import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
HELPER_PATH = ROOT / "src" / "plugins" / "random_face" / "local_comics.py"
PLUGIN_PATH = ROOT / "src" / "plugins" / "random_face" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("random_face_local_test", HELPER_PATH)
LOCAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOCAL
SPEC.loader.exec_module(LOCAL)


class RandomFaceLocalOnlyTests(unittest.TestCase):
    def test_only_local_supported_images_are_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "001.jpg"
            second = root / "002.PNG"
            first.write_bytes(b"jpg")
            second.write_bytes(b"png")
            (root / "notes.txt").write_text("not an image", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "003.jpg").write_bytes(b"nested")

            self.assertEqual(LOCAL.list_local_comics(root), [first, second])
            self.assertEqual(
                LOCAL.choose_local_comic(root, chooser=lambda files: files[-1]), second
            )

    def test_empty_or_missing_directory_has_no_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(LOCAL.choose_local_comic(root))
            self.assertIsNone(LOCAL.choose_local_comic(root / "missing"))

    def test_plugin_contains_no_remote_or_qq_face_fallback(self):
        source = PLUGIN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("httpx", source)
        self.assertNotIn("lolicon", source.lower())
        self.assertNotIn("MessageSegment.face", source)
        self.assertNotIn("音击漫画！", source)


if __name__ == "__main__":
    unittest.main()
