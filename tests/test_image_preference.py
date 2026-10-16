from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from PIL import Image

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
from image_preference import skip_point
from chatgpt_image_ui import Automation


def frame(title=True, skip=True, skip_y=42):
    image = Image.new('RGB', (850, 600), 'white')
    if title:
        image.paste(Image.open(APP / 'assets/preference-title.png'), (40, 42))
    if skip:
        image.paste(Image.open(APP / 'assets/preference-skip.png'), (740, skip_y))
    return image


class PreferenceTests(unittest.TestCase):
    def test_skip_requires_both_anchors_on_same_row(self):
        self.assertEqual(skip_point(frame()), (757, 52))
        for image in (frame(title=False), frame(skip=False), frame(skip_y=150)):
            self.assertIsNone(skip_point(image))

    def test_scaled_and_dark_mode(self):
        import PIL.ImageOps
        for scale in (.75, 1.25, 1.5, 2):
            image = frame().resize((int(850 * scale), int(600 * scale)))
            point = skip_point(image)
            self.assertIsNotNone(point, scale)
            self.assertAlmostEqual(point[0], 757 * scale, delta=3)
            self.assertAlmostEqual(point[1], 52 * scale, delta=3)
        self.assertEqual(skip_point(PIL.ImageOps.invert(frame())), (757, 52))

    def test_screenshot_clicks_skip_then_returns_fresh_frame(self):
        auto = Automation.__new__(Automation)
        auto.assert_target = Mock()
        auto.rect = Mock(return_value=(100, 200, 850, 600))
        auto.pag = Mock()
        fresh = frame(title=False, skip=False)
        auto.pag.screenshot.side_effect = [frame(), fresh]
        auto.log = Mock()
        auto.sleep = Mock()
        self.assertIs(auto.shot(), fresh)
        auto.pag.click.assert_called_once_with(857, 252)
        auto.sleep.assert_called_once_with(.4)

    def test_normal_page_never_clicks(self):
        auto = Automation.__new__(Automation)
        auto.assert_target = Mock()
        auto.rect = Mock(return_value=(0, 0, 850, 600))
        auto.pag = Mock()
        auto.pag.screenshot.return_value = frame(title=False)
        auto.shot()
        auto.pag.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
