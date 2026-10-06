"""验证滚动到底和完成判定的边界；所有桌面输入及截图均由假对象提供。"""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import chatgpt_image_ui as module
from PIL import Image, ImageChops, ImageDraw, ImageStat


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


def fake_automation():
    value = module.Automation.__new__(module.Automation)
    value.cfg = dict(module.DEFAULTS, points={"input": [350, 500]},
                     client_size=[800, 600], timeout=30)
    value.stop = threading.Event()
    value.log = Mock()
    value.Image, value.ImageChops, value.ImageStat = Image, ImageChops, ImageStat
    value.pag = Mock()
    value.assert_target = Mock()
    value.rect = Mock(return_value=(100, 200, 800, 600))
    value.sleep = Mock()
    value.hover = Mock()
    return value


def solid(level, identity=0):
    frame = Image.new("RGB", (800, 600), (level, level, level))
    # 在正文取样范围外改变一像素，区分内容相同但截图不同的对象。
    frame.putpixel((0, 0), (identity, 0, 0))
    return frame


class BottomCompletionTests(unittest.TestCase):
    def setUp(self):
        self.a = fake_automation()

    def test_scroll_uses_body_coordinates_relative_to_window(self):
        self.a.scroll_latest()
        self.a.assert_target.assert_called()
        args = self.a.pag.moveTo.call_args.args
        self.assertEqual(args[:2], (804, 500))
        self.a.pag.scroll.assert_called_once_with(-36)
        self.a.pag.click.assert_not_called()
        self.a.pag.hotkey.assert_not_called()

    def test_scroll_stays_above_expanded_input_box(self):
        self.a.cfg["points"]["input"] = [350, 250]
        self.a.scroll_latest()
        args = self.a.pag.moveTo.call_args.args
        self.assertEqual(args[:2], (804, 370))
        self.assertLess(args[1], 200 + 250)

    def test_sample_ignores_title_sidebar_and_input_but_sees_body(self):
        original = solid(255)
        outside = original.copy()
        draw = ImageDraw.Draw(outside)
        draw.rectangle((0, 0, 799, 39), fill="black")
        draw.rectangle((0, 0, 119, 599), fill="black")
        draw.rectangle((0, 530, 799, 599), fill="black")
        original_sample = self.a.content_sample(original)
        self.assertAlmostEqual(self.a.frame_delta(original_sample, self.a.content_sample(outside)), 0.0)
        body_changed = original.copy()
        ImageDraw.Draw(body_changed).rectangle((250, 120, 680, 400), fill="black")
        self.assertGreater(self.a.frame_delta(original_sample, self.a.content_sample(body_changed)), 0.6)

    def test_bottom_waits_for_two_stable_comparisons_and_returns_fresh_frame(self):
        frames = [solid(0, 1), solid(90, 2), solid(90, 3), solid(90, 4)]
        self.a.scroll_latest = Mock()
        self.a.shot = Mock(side_effect=frames)
        result = self.a.scroll_to_bottom()
        self.assertIs(result, frames[-1])
        self.assertEqual(self.a.scroll_latest.call_count, 4)
        self.assertEqual(self.a.shot.call_count, 4)

    def test_body_change_resets_consecutive_bottom_comparisons(self):
        frames = [solid(0, 1), solid(0, 2), solid(90, 3), solid(90, 4), solid(90, 5)]
        self.a.scroll_latest = Mock()
        self.a.shot = Mock(side_effect=frames)
        result = self.a.scroll_to_bottom()
        self.assertIs(result, frames[-1])
        self.assertEqual(self.a.scroll_latest.call_count, 5)

    def test_bottom_has_a_twelve_round_limit(self):
        self.a.scroll_latest = Mock()
        self.a.shot = Mock(side_effect=[solid(i * 15) for i in range(12)])
        with self.assertRaisesRegex(RuntimeError, "底部"):
            self.a.scroll_to_bottom()
        self.assertEqual(self.a.scroll_latest.call_count, 12)
        self.assertEqual(self.a.shot.call_count, 12)

    def test_static_loading_without_image_marker_never_succeeds(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        self.a.shot = Mock(return_value=solid(255))
        self.a.marker_present = Mock(return_value=False)
        self.a.scroll_latest = Mock()
        self.a.scroll_to_bottom = Mock(side_effect=AssertionError("没有完成标记时不应进入最终截图"))
        with patch.object(module.time, "monotonic", clock.monotonic), self.assertRaises(TimeoutError):
            self.a.wait_done(Image.new("RGB", (30, 30), "black"))
        self.assertGreaterEqual(clock.now, self.a.cfg["timeout"])
        self.assertGreater(self.a.scroll_latest.call_count, 1)
        self.a.scroll_to_bottom.assert_not_called()

    def test_final_scroll_losing_image_marker_does_not_return_candidate(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        candidate, missing = solid(255, 1), solid(255, 2)
        finalized = False

        def final_scroll():
            nonlocal finalized
            finalized = True
            return missing

        self.a.shot = Mock(side_effect=lambda: missing if finalized else candidate)
        self.a.marker_present = Mock(side_effect=lambda frame, _marker: frame is candidate)
        self.a.scroll_latest = Mock()
        self.a.scroll_to_bottom = Mock(side_effect=final_scroll)
        with patch.object(module.time, "monotonic", clock.monotonic), self.assertRaises(TimeoutError):
            self.a.wait_done(Image.new("RGB", (30, 30), "black"))
        self.a.scroll_to_bottom.assert_called_once()
        self.assertTrue(any(call.args[0] is missing for call in self.a.marker_present.call_args_list))

    def test_wait_done_returns_new_capture_after_final_scroll(self):
        clock = Clock()
        self.a.sleep = clock.sleep
        candidate, fresh = solid(255, 1), solid(255, 2)
        finalized = False

        def final_scroll():
            nonlocal finalized
            finalized = True
            return fresh

        self.a.shot = Mock(side_effect=lambda: fresh if finalized else candidate)
        self.a.marker_present = Mock(return_value=True)
        self.a.scroll_latest = Mock()
        self.a.scroll_to_bottom = Mock(side_effect=final_scroll)
        with patch.object(module.time, "monotonic", clock.monotonic):
            result = self.a.wait_done(Image.new("RGB", (30, 30), "black"))
        self.assertIs(result, fresh)
        self.assertIsNot(result, candidate)
        self.a.scroll_to_bottom.assert_called_once()
        self.assertIs(self.a.marker_present.call_args.args[0], fresh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
