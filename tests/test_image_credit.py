import unittest

from PIL import Image, ImageFont

from src.image_credit import IMAGE_CREDIT_HEIGHT, append_image_credit


class ImageCreditTests(unittest.TestCase):
    def test_credit_is_appended_without_changing_source_pixels(self):
        source = Image.new("RGB", (320, 180), (12, 34, 56))
        result = append_image_credit(source, ImageFont.load_default())

        self.assertEqual(result.size, (320, 180 + IMAGE_CREDIT_HEIGHT))
        self.assertEqual(result.getpixel((20, 20)), (12, 34, 56))
        self.assertNotEqual(result.getpixel((160, 210)), (12, 34, 56))


if __name__ == "__main__":
    unittest.main()
