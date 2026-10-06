"""下载菜单重试与远端截图确认的离线测试，无真实桌面输入。"""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import chatgpt_image_ui as ui
from server import make_app
from PIL import Image


class ReviewTests(unittest.TestCase):
    def test_review_wait_retry_and_stale_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / 'preview.png'
            result = root / 'result.png'
            Image.new('RGB', (80, 80), 'gray').save(preview)
            Image.new('RGB', (80, 80), 'red').save(result)
            calls, actions = [], []
            def runner(prompt, cfg, config, log, reference=None, review=None):
                calls.append(prompt)
                if prompt == '等待确认':
                    actions.append(review(preview, '两次右击失败'))
                    actions.append(review(preview, '继续等待后再次确认'))
                return result
            app = make_app(root / 'config.json', runner=runner, validate_startup=False)
            client = app.test_client()
            manager = app.extensions['job_manager']
            def wait(job_id, status, version=None):
                for _ in range(300):
                    snapshot = client.get('/jobs/' + job_id).get_json()
                    if snapshot['status'] == status and (version is None or snapshot.get('review_version') == version):
                        return snapshot
                    time.sleep(.01)
                self.fail(str(snapshot))
            try:
                job = client.post('/jobs', json={'prompt': '等待确认', 'request_id': 'once'}).get_json()['id']
                first = wait(job, 'review', 1)
                with client.get(first['preview_url']) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, preview.read_bytes())
                    self.assertEqual(response.mimetype, 'image/png')
                self.assertEqual(client.get('/jobs/' + job + '/image').status_code, 409)
                queued = client.post('/jobs', json={'prompt': '下一任务'}).get_json()['id']
                self.assertEqual(client.get('/jobs/' + queued).get_json()['status'], 'queued')
                self.assertEqual(calls, ['等待确认'])
                endpoint = '/jobs/' + job + '/review'
                self.assertEqual(client.post(endpoint, json={'action': []}).status_code, 400)
                self.assertEqual(client.post(endpoint, json={'action': 'retry', 'version': 0}).status_code, 409)
                self.assertEqual(client.post(endpoint, json={'action': 'wait', 'version': 1}).status_code, 202)
                wait(job, 'review', 2)
                self.assertEqual(client.post(endpoint, json={'action': 'retry', 'version': 1}).status_code, 409)
                self.assertEqual(client.post(endpoint, json={'action': 'retry', 'version': 2}).status_code, 202)
                wait(job, 'done')
                wait(queued, 'done')
                self.assertEqual(actions, ['wait', 'retry'])
                self.assertEqual(calls, ['等待确认', '下一任务'])
            finally:
                manager.close(timeout=2)

    def fake_download(self, root):
        a = ui.Automation.__new__(ui.Automation)
        a.cfg = dict(ui.DEFAULTS, download_dir=str(root))
        a.config_path = root / 'config.json'
        a.log = Mock()
        frame = Image.new('RGB', (800, 600), 'gray')
        a.shot = Mock(return_value=frame)
        a.open_image_menu = Mock(return_value=(100, 200))
        a.wait_download = Mock(return_value=root / 'downloaded.png')
        a.scroll_to_bottom = Mock()
        a.hover = Mock()
        a.sleep = Mock()
        return a, frame

    def test_second_right_click_succeeds_without_remote_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a, frame = self.fake_download(root)
            a.click_download_copy = Mock(side_effect=[ui.DownloadMenuNotFound('missing'), None])
            a.request_download_review = Mock()
            self.assertEqual(a.download_image(frame, None, root), root / 'downloaded.png')
            self.assertEqual(a.open_image_menu.call_count, 2)
            a.request_download_review.assert_not_called()
            a.wait_download.assert_called_once()

    def test_retry_continues_existing_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a, frame = self.fake_download(root)
            a.click_download_copy = Mock(side_effect=[ui.DownloadMenuNotFound(), ui.DownloadMenuNotFound(), None])
            a.request_download_review = Mock(return_value='retry')
            a.new_chat = Mock()
            a.send = Mock()
            self.assertEqual(a.download_image(frame, None, root), root / 'downloaded.png')
            a.request_download_review.assert_called_once()
            self.assertEqual(a.open_image_menu.call_count, 3)
            a.new_chat.assert_not_called()
            a.send.assert_not_called()

    def test_wait_resumes_generation_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a, frame = self.fake_download(root)
            a.click_download_copy = Mock(side_effect=[ui.DownloadMenuNotFound(), ui.DownloadMenuNotFound(), None])
            a.request_download_review = Mock(return_value='wait')
            a.wait_done = Mock(return_value=frame)
            a.download_image(frame, None, root)
            a.wait_done.assert_called_once_with(None, output=root)
            a.wait_download.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
