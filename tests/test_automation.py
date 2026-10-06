"""测试真实图案匹配和流程边界；不点击或截取实际桌面。"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import chatgpt_image_ui as module
import pyscreeze
from PIL import Image, ImageChops, ImageDraw, ImageStat


def images():
    marker = Image.new("RGB", (32, 32), "#205080")
    draw = ImageDraw.Draw(marker)
    draw.line((6, 9, 16, 25, 26, 9), fill="white", width=3)
    draw.rectangle((4, 27, 28, 30), outline="#ff8040", width=2)
    frame = Image.new("RGB", (800, 600), "#ededed")
    frame.paste(marker, (500, 300))
    return marker, frame


class Clock:
    now = 0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


def auto(tmp):
    value = module.Automation.__new__(module.Automation)
    value.cfg = dict(module.DEFAULTS, points={"new_chat": [10, 10], "input": [50, 50], "send": [90, 90]},
                     client_size=[800, 600], timeout=60, output_dir=str(tmp), download_dir=str(tmp))
    value.config_path = tmp / "config.json"
    value.stop = threading.Event()
    value.log = Mock()
    value.Image, value.ImageChops, value.ImageStat = Image, ImageChops, ImageStat
    value.pag = Mock()
    value.pag.ImageNotFoundException = pyscreeze.ImageNotFoundException
    value.pag.locate = pyscreeze.locate
    value.pag.center = pyscreeze.center
    value.clipboard = Mock()
    value.prepare = Mock()
    value.assert_target = Mock()
    value.rect = Mock(return_value=(0, 0, 800, 600))
    value.click = Mock()
    value.hover = Mock()
    value.review = None
    return value


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.a = auto(self.tmp)
        self.marker, self.frame = images()
        self.marker.save(self.tmp / "done_marker.png")
        self.marker.save(self.tmp / "send_marker.png")
        self.marker.save(self.tmp / "download_menu.png")

    def test_real_opencv_marker_matching(self):
        self.assertTrue(self.a.marker_present(self.frame, self.marker))
        self.assertFalse(self.a.marker_present(Image.new("RGB", (800, 600), "white"), self.marker))

    def test_send_relocates_moved_button(self):
        self.a.shot = Mock(return_value=self.frame)
        self.a.send()
        self.a.pag.click.assert_called_once_with(516, 316)
        self.a.click.assert_not_called()

    def test_unavailable_send_reports_failure(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        self.a.shot = Mock(return_value=Image.new("RGB", (800, 600), "white"))
        with patch.object(module.time, "monotonic", clock.monotonic), self.assertRaises(RuntimeError):
            self.a.send()
        self.a.pag.click.assert_not_called()

    def test_static_loading_without_marker_times_out(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        self.a.shot = Mock(return_value=Image.new("RGB", (800, 600), "white"))
        with patch.object(module.time, "monotonic", clock.monotonic), self.assertRaises(TimeoutError):
            self.a.wait_done(self.marker)

    def test_done_requires_marker_and_stability(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        self.a.shot = Mock(return_value=self.frame)
        with patch.object(module.time, "monotonic", clock.monotonic):
            frame = self.a.wait_done(self.marker)
        self.assertIs(frame, self.frame)
        self.assertGreaterEqual(clock.now, 4.5)
        self.assertLess(clock.now, 60)

    def test_disappearing_marker_resets_timer(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        self.a.shot = Mock(return_value=self.frame)
        self.a.marker_present = lambda *_: not 3 <= clock.now < 4.5
        with patch.object(module.time, "monotonic", clock.monotonic):
            self.a.wait_done(self.marker)
        self.assertGreaterEqual(clock.now, 6)

    def test_old_marker_prevents_sending(self):
        self.a.sleep = Mock()
        self.a.shot = Mock(return_value=self.frame)
        with self.assertRaisesRegex(RuntimeError, "仍匹配到完成标记"):
            self.a.run("画一只猫")
        self.a.clipboard.copy.assert_not_called()

    def test_chinese_paste_save_and_cleanup(self):
        prompt = "画一只在太空的猫\n背景为蓝色星云"
        self.a.sleep = Mock()
        self.a.new_chat = Mock()
        self.a.send = Mock()
        self.a.wait_done = Mock(return_value=self.frame)
        downloaded = self.tmp / "downloaded.png"
        Image.new("RGB", (120, 90), "blue").save(downloaded)
        self.a.download_image = Mock(return_value=downloaded)
        result = self.a.run(prompt)
        self.a.clipboard.copy.assert_called_once_with(prompt)
        self.a.pag.hotkey.assert_called_once_with("ctrl", "v")
        self.assertEqual(self.a.new_chat.call_count, 2)
        self.assertTrue(result.is_file())
        with Image.open(result) as generated:
            self.assertEqual(generated.size, (120, 90))
        self.assertEqual(result, downloaded)
        self.a.download_image.assert_called_once()
        with Image.open(next(self.tmp.glob("screenshot_*.png"))) as record:
            self.assertEqual(record.size, (800, 600))

    def test_timeout_saves_diagnostic_without_cleanup(self):
        self.a.sleep = Mock()
        self.a.new_chat = Mock()
        self.a.send = Mock()
        self.a.wait_done = Mock(side_effect=TimeoutError("timeout"))
        self.a.shot = Mock(return_value=self.frame)
        with self.assertRaises(TimeoutError):
            self.a.run("画猫")
        self.assertEqual(self.a.new_chat.call_count, 1)
        self.assertEqual(len(list(self.tmp.glob("timeout_*.png"))), 1)
        self.assertEqual(len(list(self.tmp.glob("chatgpt_*.png"))), 0)

    def test_stop_cancels_wait(self):
        self.a.stop.set()
        with self.assertRaises(module.Cancelled):
            module.Automation.sleep(self.a, 0.01)

    def test_config_roundtrip(self):
        module.save_config(self.a.config_path, self.a.cfg)
        loaded = module.load_config(self.a.config_path)
        self.assertEqual(loaded["points"], self.a.cfg["points"])
        self.assertEqual(loaded["client_size"], [800, 600])


if __name__ == "__main__":
    unittest.main(verbosity=2)
