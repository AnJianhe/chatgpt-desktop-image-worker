"""校准点保存的纯函数回归测试；不创建窗口或访问真实桌面。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import chatgpt_image_ui as ui


class PointSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config_path = self.directory / "config.json"
        self.cfg = dict(ui.DEFAULTS, points={"new_chat": [25, 34]},
                        client_size=[500, 400])
        ui.save_config(self.config_path, self.cfg)
        self.screen = Image.new("RGB", (900, 700), "#ededed")
        self.rect = (120, 80, 500, 400)

    def unchanged_after_rejection(self, key, point, rect=None, exception=ValueError):
        before_cfg = copy.deepcopy(self.cfg)
        before_file = self.config_path.read_bytes()
        marker = self.directory / "send_marker.png"
        before_marker = marker.read_bytes() if marker.exists() else None
        with self.assertRaises(exception):
            ui.save_point_selection(self.cfg, self.config_path, key,
                                    self.screen, rect or self.rect, point)
        self.assertEqual(self.cfg, before_cfg)
        self.assertEqual(self.config_path.read_bytes(), before_file)
        self.assertEqual(marker.read_bytes() if marker.exists() else None, before_marker)

    def test_nonzero_client_origin_saves_relative_point_and_preserves_new_chat(self):
        ui.save_point_selection(self.cfg, self.config_path, "input", self.screen,
                                self.rect, (333, 250))
        self.assertEqual(self.cfg["points"]["input"], [213, 170])
        self.assertEqual(self.cfg["points"]["new_chat"], [25, 34])
        self.assertEqual(self.cfg["client_size"], [500, 400])
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved, self.cfg)

    def test_four_outside_edges_do_not_change_configuration(self):
        for point in ((119, 200), (620, 200), (300, 79), (300, 480)):
            with self.subTest(point=point):
                self.unchanged_after_rejection("input", point)

    def test_inclusive_left_top_and_last_client_pixel_are_valid(self):
        for point, expected in (((120, 80), [0, 0]), ((619, 479), [499, 399])):
            with self.subTest(point=point):
                ui.save_point_selection(self.cfg, self.config_path, "hover",
                                        self.screen, self.rect, point)
                self.assertEqual(self.cfg["points"]["hover"], expected)

    def test_changed_client_size_does_not_change_configuration(self):
        self.unchanged_after_rejection("input", (333, 250),
                                       rect=(120, 80, 501, 400), exception=RuntimeError)

    def test_client_outside_screenshot_is_rejected(self):
        for rect in ((-1, 80, 500, 400), (120, -1, 500, 400),
                     (401, 80, 500, 400), (120, 301, 500, 400)):
            with self.subTest(rect=rect):
                self.unchanged_after_rejection("input", (333, 250),
                                               rect=rect, exception=RuntimeError)

    def test_send_marker_uses_absolute_screenshot_crop(self):
        absolute_point = (460, 390)
        button = Image.new("RGB", (32, 32), "#103c88")
        draw = ImageDraw.Draw(button)
        draw.polygon(((16, 3), (4, 17), (12, 17), (12, 28),
                      (20, 28), (20, 17), (28, 17)), fill="white")
        self.screen.paste(button, (444, 374))
        ui.save_point_selection(self.cfg, self.config_path, "send", self.screen,
                                self.rect, absolute_point)
        with Image.open(self.directory / "send_marker.png") as saved:
            self.assertEqual(saved.size, button.size)
            self.assertEqual(saved.convert("RGB").tobytes(), button.tobytes())
        self.assertEqual(self.cfg["points"]["send"], [340, 310])
        self.assertEqual(self.cfg["points"]["new_chat"], [25, 34])

    def test_plain_background_send_point_preserves_config_and_existing_marker(self):
        Image.new("RGB", (32, 32), "red").save(self.directory / "send_marker.png")
        self.unchanged_after_rejection("send", (460, 390))


if __name__ == "__main__":
    unittest.main(verbosity=2)
