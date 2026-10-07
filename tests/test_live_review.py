"""Confirmation remains open while a single desktop worker detects completion."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PIL import Image
import chatgpt_image_ui as ui
from server import make_app, Job, ReviewChannel
from test_sixty_second_review import Clock, automation


class DetectorTests(unittest.TestCase):
    def setup(self, clock, decision=None, ready_at=63):
        a=automation(clock)
        events=[]
        class Channel:
            supports_live_detection=True
            def begin(self,*args):events.append(('begin',clock.now));return 1
            def poll(self,version):
                if decision and clock.now>=61 and not any(e[0]=='choice' for e in events):
                    events.append(('choice',clock.now));return decision
                return None
            def complete(self,version):events.append(('auto',clock.now));return 'auto'
        a.review=Channel();a.capture_review=Mock(return_value=Path('preview.png'))
        a.marker_present=Mock(side_effect=lambda *args:clock.now>=ready_at)
        return a,events

    def test_sixty_second_pending_review_still_detects_and_downloads(self):
        clock=Clock();a,events=self.setup(clock)
        with patch.object(ui.time,'monotonic',clock.monotonic):
            self.assertEqual(a.wait_done(None,output=Path('.')),'screenshot')
        self.assertEqual([e[0] for e in events],['begin','auto'])
        self.assertGreaterEqual(events[0][1],60);self.assertLess(events[0][1],61)
        self.assertLess(events[-1][1],67)
        a.scroll_to_bottom.assert_called_once()

    def test_manual_wait_at_sixty_one_keeps_detector_running(self):
        clock=Clock();a,events=self.setup(clock,'wait')
        with patch.object(ui.time,'monotonic',clock.monotonic):a.wait_done(None,output=Path('.'))
        self.assertEqual([e[0] for e in events],['begin','choice'])
        self.assertGreaterEqual(clock.now,63);self.assertLess(clock.now,67)
        a.scroll_to_bottom.assert_called_once()

    def test_manual_retry_stops_detection_before_marker_and_bypasses_matching(self):
        clock=Clock();a,events=self.setup(clock,'retry',ready_at=999)
        with patch.object(ui.time,'monotonic',clock.monotonic):a.wait_done(None,output=Path('.'))
        self.assertLess(clock.now,62);a.scroll_to_bottom.assert_not_called()
        self.assertEqual([e[0] for e in events],['begin','choice'])

    def test_confirmed_end_stops_without_download(self):
        clock=Clock();a,events=self.setup(clock,'end')
        a.download_image=Mock()
        with patch.object(ui.time,'monotonic',clock.monotonic),self.assertRaises(ui.NotGenerated):
            a.wait_done(None,output=Path('.'))
        self.assertLess(clock.now,62);a.scroll_to_bottom.assert_not_called();a.download_image.assert_not_called()
        self.assertNotIn('auto',[e[0] for e in events])

    def test_pending_review_does_not_timeout_at_original_deadline(self):
        clock=Clock();a,events=self.setup(clock,ready_at=605)
        with patch.object(ui.time,'monotonic',clock.monotonic):a.wait_done(None,output=Path('.'))
        self.assertEqual([e[0] for e in events],['begin','auto'])
        self.assertGreater(clock.now,600);self.assertLess(clock.now,610)

    def test_manual_end_wins_during_final_frame_validation(self):
        clock=Clock();a,events=self.setup(clock)
        a.review.complete=Mock(return_value='end')
        with patch.object(ui.time,'monotonic',clock.monotonic),self.assertRaises(ui.NotGenerated):a.wait_done(None,output=Path('.'))
        a.review.complete.assert_called_once_with(1)


class ArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.preview=self.root/'preview.png';Image.new('RGB',(20,20)).save(self.preview)
        self.app=make_app(self.root/'config.json',validate_startup=False)
        self.manager=self.app.extensions['job_manager'];self.client=self.app.test_client()
        # Register a controlled job without putting it into the desktop queue.
        self.job=Job(id='testjob',prompt='原提示词',original_prompt='原提示词')
        with self.manager.lock:self.manager.jobs[self.job.id]=self.job
        self.channel=ReviewChannel(self.manager,self.job.id)

    def tearDown(self):self.manager.close(2);self.tmp.cleanup()

    def begin(self):return self.channel.begin(self.preview,'60秒截图，自动检测继续')

    def decision(self,action,version,confirmed=False):
        return self.client.post('/jobs/testjob/review',json={'action':action,'version':version,'confirmed':confirmed})

    def test_pending_auto_claim_closes_review_and_rejects_late_decision(self):
        version=self.begin();self.assertIsNone(self.channel.poll(version))
        self.assertEqual(self.channel.complete(version),'auto')
        self.assertEqual(self.manager.snapshot('testjob')['status'],'running')
        self.assertFalse(self.job.review_active)
        self.assertEqual(self.decision('end',version,True).status_code,409)
        self.assertEqual(self.manager.history.get(job_id='testjob')['status'],'running')

    def test_confirmed_end_wins_over_auto_and_retry(self):
        version=self.begin()
        self.assertEqual(self.decision('end',version).status_code,400)
        self.assertIsNone(self.channel.poll(version))
        self.assertEqual(self.decision('end',version,True).status_code,202)
        self.assertEqual(self.channel.complete(version),'end')
        with self.assertRaises(RuntimeError):self.channel.complete(version)

    def test_wait_does_not_prevent_auto_retry_remains_explicit(self):
        version=self.begin();self.assertEqual(self.decision('wait',version).status_code,202)
        self.assertEqual(self.channel.complete(version),'auto')
        next_version=self.begin();self.assertEqual(self.decision('retry',next_version).status_code,202)
        self.assertEqual(self.channel.complete(next_version),'retry')
        self.assertEqual(self.decision('wait',version).status_code,409)

    def test_simultaneous_actions_have_one_winner(self):
        version=self.begin();barrier=threading.Barrier(2);results=[]
        def automatic():barrier.wait();results.append(('auto',self.channel.complete(version)))
        def manual():
            with self.app.test_client() as client:
                barrier.wait();r=client.post('/jobs/testjob/review',json={'action':'end','version':version,'confirmed':True});results.append(('manual',r.status_code))
        a=threading.Thread(target=automatic);b=threading.Thread(target=manual);a.start();b.start();a.join();b.join()
        winner=dict(results)
        self.assertIn((winner['auto'],winner['manual']),[('auto',409),('end',202)])
        self.assertFalse(self.job.review_active)


if __name__=='__main__':unittest.main()
