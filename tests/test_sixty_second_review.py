"""离线验证 60 秒确认、直接下载、继续识别；不触发真实桌面。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import chatgpt_image_ui as ui

class Clock:
    now=0.0
    def sleep(self,sec):self.now+=sec
    def monotonic(self):return self.now

def automation(clock):
    a=ui.Automation.__new__(ui.Automation)
    a.cfg=dict(ui.DEFAULTS,timeout=600)
    a.review=Mock()
    a.sleep=clock.sleep
    a.scroll_latest=Mock()
    a.hover=Mock()
    a.log=Mock()
    a.shot=Mock(return_value='screenshot')
    a.content_sample=Mock(return_value='sample')
    a.frame_delta=Mock(return_value=0)
    a.marker_present=Mock(return_value=False)
    a.scroll_to_bottom=Mock()
    return a

class ReviewTests(unittest.TestCase):
    def test_at_sixty_seconds_retry_bypasses_failed_marker(self):
        clock=Clock();a=automation(clock);times=[]
        def review(*args):times.append(clock.now);return 'retry'
        a.request_download_review=Mock(side_effect=review)
        with patch.object(ui.time,'monotonic',clock.monotonic):
            self.assertEqual(a.wait_done(None,output=Path('.')),'screenshot')
        self.assertEqual(len(times),1)
        self.assertGreaterEqual(times[0],60)
        self.assertLess(times[0],61)
        a.scroll_to_bottom.assert_not_called()

    def test_wait_resumes_recognition_and_reviews_again(self):
        clock=Clock();a=automation(clock);times=[]
        def review(*args):
            times.append(clock.now)
            if len(times)==1:
                clock.sleep(900) # 人看截图的等待时间不能导致自动超时。
                return 'wait'
            return 'retry'
        a.request_download_review=Mock(side_effect=review)
        with patch.object(ui.time,'monotonic',clock.monotonic):
            a.wait_done(None,output=Path('.'))
        self.assertEqual(len(times),2)
        self.assertGreaterEqual(times[1]-times[0],960)
        self.assertLess(times[1]-times[0],961)

    def test_automatic_completion_before_sixty_seconds(self):
        clock=Clock();a=automation(clock);a.marker_present=Mock(return_value=True)
        a.request_download_review=Mock()
        with patch.object(ui.time,'monotonic',clock.monotonic):
            a.wait_done(None,output=Path('.'))
        self.assertLess(clock.now,60)
        a.request_download_review.assert_not_called()

    def test_unstable_bottom_still_triggers_sixty_second_review(self):
        clock=Clock();a=automation(clock);a.marker_present=Mock(return_value=True)
        a.scroll_to_bottom=Mock(side_effect=ui.BottomNotStable('unstable'))
        a.request_download_review=Mock(return_value='retry')
        with patch.object(ui.time,'monotonic',clock.monotonic):
            a.wait_done(None,output=Path('.'))
        a.request_download_review.assert_called_once()
        self.assertGreaterEqual(clock.now,60)
        self.assertLess(clock.now,61)

if __name__=='__main__':unittest.main()
