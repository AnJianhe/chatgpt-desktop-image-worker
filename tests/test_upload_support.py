"""参考图网络传输、幂等、任务关联、剪贴板和提速的离线验证。不会操控实际桌面。"""
import ctypes
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import chatgpt_image_ui as ui
from server import make_app
from PIL import Image, ImageChops, ImageStat


def image_data(color='red', fmt='PNG'):
    data = io.BytesIO()
    Image.new('RGB', (80, 60), color).save(data, format=fmt)
    return data.getvalue()


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.calls = []
        self.result = self.folder / 'result.png'
        self.result.write_bytes(image_data())
        def runner(prompt, cfg, config, log, reference=None, review=None):
            self.calls.append((prompt, reference))
            if reference:
                with Image.open(reference) as image:
                    image.load()
                    self.assertEqual(image.size, (80, 60))
            log('模拟生成完成')
            return self.result
        self.app = make_app(self.folder / 'config.json', runner=runner, validate_startup=False)
        self.client = self.app.test_client()
        self.manager = self.app.extensions['job_manager']

    def tearDown(self):
        self.manager.close(timeout=2)
        self.tmp.cleanup()

    def upload(self, value=None, upload_id='a' * 32):
        with io.BytesIO(value or image_data()) as source:
            response = self.client.post('/uploads', data={'image': (source, '参考图.png'), 'upload_id': upload_id})
        # Oversized multipart requests can be rejected before Flask reads them.
        # Close the test client's generated spool, including the 413 case.
        response.request.environ['wsgi.input'].close()
        return response

    def wait(self, job_id):
        for _ in range(200):
            status = self.client.get('/jobs/' + job_id).get_json()
            if status['status'] in {'done', 'error'}:
                return status
            time.sleep(.01)
        self.fail('模拟任务超时')

    def test_upload_job_and_original_response(self):
        self.assertEqual(self.upload().status_code, 201)
        data = {'prompt': '把参考图变成水彩', 'request_id': 'same', 'upload_id': 'a' * 32}
        created = self.client.post('/jobs', json=data)
        self.assertEqual(created.status_code, 202)
        job_id = created.get_json()['id']
        status = self.wait(job_id)
        self.assertEqual(status['status'], 'done', status)
        self.assertEqual(self.calls[0][0], data['prompt'])
        self.assertEqual(self.calls[0][1], self.folder / 'uploads' / ('a' * 32 + '.png'))
        with self.client.get(status['image_url']) as response:
            self.assertEqual(response.data, self.result.read_bytes())
        self.assertEqual(self.client.post('/jobs', json=data).get_json()['id'], job_id)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.upload(image_data('blue')).status_code, 409)
        self.assertEqual(self.upload(upload_id='b' * 32).status_code, 201)
        data['upload_id'] = 'b' * 32
        self.assertEqual(self.client.post('/jobs', json=data).status_code, 409)

    def test_plain_prompt_still_works(self):
        response = self.client.post('/jobs', json={'prompt': '画猫'})
        self.assertEqual(self.wait(response.get_json()['id'])['status'], 'done')
        self.assertEqual(self.calls, [('画猫', None)])

    def test_extraction_requires_reference(self):
        self.assertEqual(self.client.post('/jobs', json={'prompt': '提取印花', 'tool': 'extract'}).status_code, 400)
        self.assertEqual(self.upload().status_code, 201)
        created = self.client.post('/jobs', json={'prompt': '提取印花', 'tool': 'extract', 'upload_id': 'a' * 32})
        self.assertEqual(created.status_code, 202)
        self.assertEqual(self.wait(created.get_json()['id'])['status'], 'done')

    def test_corrupt_missing_and_invalid_images(self):
        self.assertEqual(self.upload(b'not an image').status_code, 400)
        self.assertEqual(self.upload(upload_id='../outside').status_code, 400)
        self.assertEqual(self.upload(image_data(fmt='GIF')).status_code, 400)
        self.assertEqual(self.client.post('/uploads', data={}).status_code, 400)
        for upload_id in ('../outside', 'b' * 32, 9):
            self.assertEqual(self.client.post('/jobs', json={'prompt': '改图', 'upload_id': upload_id}).status_code, 400)
        self.assertEqual(self.client.post('/jobs', data=b'x' * 65537, content_type='application/json').status_code, 413)

    def test_large_upload_and_jpeg_conversion(self):
        self.assertEqual(self.upload(b'x' * (20 * 1024 * 1024 + 1)).status_code, 413)
        self.assertEqual(self.upload(image_data(fmt='JPEG')).status_code, 201)
        with Image.open(self.folder / 'uploads' / ('a' * 32 + '.png')) as image:
            self.assertEqual(image.format, 'PNG')
            image.load()


class DesktopOfflineTests(unittest.TestCase):
    def setup_auto(self, folder):
        a = ui.Automation.__new__(ui.Automation)
        a.cfg = dict(ui.DEFAULTS, points={'input': [100, 500], 'send': [500, 500], 'new_chat': [10, 10]}, client_size=[800, 600], output_dir=str(folder), download_dir=str(folder))
        a.config_path = folder / 'config.json'
        a.Image, a.ImageChops, a.ImageStat = Image, ImageChops, ImageStat
        a.log = Mock()
        a.stop = threading.Event()
        a.pag = Mock()
        a.clipboard = Mock()
        a.window = Mock(hwnd=123)
        a.click = Mock()
        a.assert_target = Mock()
        return a

    def test_image_paste_uses_dib_and_waits_for_changed_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            ref = folder / 'ref.png'
            ref.write_bytes(image_data())
            a = self.setup_auto(folder)
            frames = [Image.new('RGB', (800, 600), 'white'), Image.new('RGB', (800, 600), 'gray')]
            a.shot = Mock(side_effect=lambda: frames.pop(0) if len(frames) > 1 else frames[0])
            clock = [0.0]
            a.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            allocation = ctypes.create_string_buffer(200_000)
            user, kernel = Mock(), Mock()
            user.OpenClipboard.return_value = True
            user.EmptyClipboard.return_value = True
            user.SetClipboardData.return_value = 1
            kernel.GlobalAlloc.return_value = 777
            kernel.GlobalLock.return_value = ctypes.addressof(allocation)
            with patch.object(ui.ctypes, 'WinDLL', side_effect=lambda name, **kw: user if name == 'user32' else kernel), patch.object(ui.time, 'monotonic', side_effect=lambda: clock[0]):
                a.paste_reference(ref)
            user.SetClipboardData.assert_called_once_with(8, 777)
            user.CloseClipboard.assert_called_once()
            kernel.GlobalFree.assert_not_called()
            self.assertEqual(int.from_bytes(allocation.raw[:4], 'little'), 40)
            a.pag.hotkey.assert_called_once_with('ctrl', 'v')
            self.assertGreaterEqual(clock[0], 2)

    def test_reference_is_pasted_before_prompt_and_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            a = self.setup_auto(folder)
            for filename in ('done_marker.png', 'download_menu.png'):
                (folder / filename).write_bytes(image_data())
            sequence = []
            a.prepare = Mock()
            a.new_chat = Mock()
            a.sleep = Mock()
            a.paste_reference = Mock(side_effect=lambda ref: sequence.append('image'))
            a.clipboard.copy = Mock(side_effect=lambda prompt: sequence.append('prompt'))
            a.send = Mock(side_effect=lambda **kw: sequence.append('send'))
            a.wait_done = Mock(return_value=Image.new('RGB', (800, 600)))
            result = folder / 'original.png'
            result.write_bytes(image_data())
            a.download_image = Mock(return_value=result)
            self.assertEqual(a.run('修改参考图', folder / 'ref.png'), result)
            self.assertEqual(sequence, ['image', 'prompt', 'send'])
            a.send.assert_called_once_with(timeout=60)
            self.assertEqual(a.new_chat.call_count, 2)

    def test_fast_completion_keeps_bottom_recheck(self):
        with tempfile.TemporaryDirectory() as temporary:
            a = self.setup_auto(Path(temporary))
            clock = [0.0]
            a.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            a.shot = Mock(return_value=Image.new('RGB', (800, 600), 'gray'))
            a.scroll_latest = Mock()
            a.scroll_to_bottom = Mock()
            a.hover = Mock()
            a.marker_present = Mock(return_value=True)
            with patch.object(ui.time, 'monotonic', side_effect=lambda: clock[0]):
                a.wait_done(None)
            self.assertLess(clock[0], 7)
            a.scroll_to_bottom.assert_called_once()
            self.assertEqual(a.cfg['confidence'], .9)


if __name__ == '__main__':
    unittest.main(verbosity=2)
