"""Heartbeat HTTP tests with an idle fake worker; never touch ChatGPT or cloudflared."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from server import make_app

spec = importlib.util.spec_from_file_location('watchdog_http_tests', ROOT / 'app/tunnel_watchdog.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class HeartbeatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        def runner(*args, **kwargs):
            self.calls.append(args)
            raise AssertionError('Heartbeat must not submit a drawing')
        self.app = make_app(Path(self.tmp.name) / 'config.json', runner=runner, validate_startup=False)
        self.client = self.app.test_client()
        self.manager = self.app.extensions['job_manager']
        self.tracker = self.app.extensions['tunnel_heartbeat']

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()
        self.assertEqual(self.calls, [])

    def post(self, host='current.trycloudflare.com', server_id=None):
        return self.client.post('/api/tunnel-heartbeat', base_url='https://' + host,
                                json={'server_id': server_id or self.tracker.server_id, 'nonce':'packet-1'})

    def test_public_page_contains_instance_and_five_second_heartbeat(self):
        response = self.client.get('/')
        text = response.get_data(as_text=True)
        self.assertIn(self.tracker.server_id, text)
        with self.client.get('/static/workflow.js') as response:
            self.assertIn('setInterval(heartbeat,5000)', response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_browser_packet_is_recorded_only_for_current_host(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['recorded'])
        self.assertEqual(response.json['nonce'], 'packet-1')
        current = self.client.get('/api/tunnel-heartbeat?host=current.trycloudflare.com', base_url='http://127.0.0.1')
        self.assertLess(current.json['browser_heartbeat_age'], 1)
        other = self.client.get('/api/tunnel-heartbeat?host=old.trycloudflare.com', base_url='http://127.0.0.1')
        self.assertIsNone(other.json['browser_heartbeat_age'])

    def test_local_browser_cannot_keep_public_tunnel_alive(self):
        response = self.post('127.0.0.1')
        self.assertFalse(response.json['recorded'])
        self.assertIsNone(self.tracker.age('current.trycloudflare.com'))

    def test_old_page_instance_and_invalid_payload_are_rejected(self):
        self.assertEqual(self.post(server_id='previous-server').status_code, 409)
        self.assertIsNone(self.tracker.age('current.trycloudflare.com'))
        for payload in (None, [], {}, {'server_id': self.tracker.server_id}, {'server_id': self.tracker.server_id, 'nonce': 'x' * 129}):
            response = self.client.post('/api/tunnel-heartbeat', json=payload)
            self.assertIn(response.status_code, (400, 409))

    def test_get_echoes_fresh_challenge_and_is_not_cached(self):
        response = self.client.get('/api/tunnel-heartbeat?nonce=new-challenge')
        self.assertEqual(response.json['nonce'], 'new-challenge')
        self.assertEqual(response.json['server_id'], self.tracker.server_id)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_dead_worker_does_not_report_successful_heartbeat(self):
        with patch.object(self.manager.worker, 'is_alive', return_value=False):
            self.assertEqual(self.client.get('/api/tunnel-heartbeat').status_code, 503)
            self.assertEqual(self.post().status_code, 503)
        self.assertIsNone(self.tracker.age('current.trycloudflare.com'))

    def test_watchdog_http_contract_accepts_active_and_browser_packets(self):
        def get_text(url, timeout=2):
            parsed = urlsplit(url)
            response = self.client.get(parsed.path + '?' + parsed.query,
                                       base_url=parsed.scheme + '://' + parsed.netloc)
            self.assertEqual(response.status_code, 200)
            return response.get_data(as_text=True)
        with patch.object(watchdog, 'get_text', side_effect=get_text):
            local = watchdog.local_heartbeat('http://127.0.0.1', 'https://current.trycloudflare.com')
            self.assertIsNone(local['browser_heartbeat_age'])
            policy = watchdog.HeartbeatPolicy(0)
            ok, _ = watchdog.probe_public('https://current.trycloudflare.com', local['server_id'])
            self.assertFalse(policy.observe(29, probe_ok=ok))
            self.post()
            local = watchdog.local_heartbeat('http://127.0.0.1', 'https://current.trycloudflare.com')
            self.assertFalse(policy.observe(55, browser_age=local['browser_heartbeat_age']))
            self.assertTrue(policy.observe(86))


if __name__ == '__main__':
    unittest.main(verbosity=2)
