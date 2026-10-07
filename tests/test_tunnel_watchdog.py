"""Offline tests: never start cloudflared or expose a port."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

SOURCE = Path(__file__).resolve().parents[1] / "app/tunnel_watchdog.py"
spec = importlib.util.spec_from_file_location("tunnel_watchdog", SOURCE)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def test_rate_limit_cooldown_survives_restart_and_escalates(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(watchdog.time, 'time', return_value=1000):
            path = Path(folder) / 'cooldown.json'
            cooldown = watchdog.RateLimitCooldown(path)
            cooldown.record()
            self.assertEqual(cooldown.remaining(), 300)
            restored = watchdog.RateLimitCooldown(path)
            self.assertEqual(restored.remaining(), 300)
            restored.record()
            self.assertEqual(restored.remaining(), 600)
            restored.record()
            self.assertEqual(restored.remaining(), 1200)
            for _ in range(3):
                restored.record()
                self.assertEqual(restored.remaining(), 1800)

    def test_expired_cooldown_keeps_backoff_until_a_tunnel_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cooldown.json'
            with patch.object(watchdog.time, 'time', return_value=1000):
                cooldown = watchdog.RateLimitCooldown(path)
                cooldown.record()
            with patch.object(watchdog.time, 'time', return_value=1400):
                restored = watchdog.RateLimitCooldown(path)
                self.assertEqual(restored.remaining(), 0)
                restored.record()
                self.assertEqual(restored.remaining(), 600)
                restored.clear()
                self.assertFalse(path.exists())
                self.assertEqual(watchdog.RateLimitCooldown(path).remaining(), 0)

    def test_invalid_cooldown_file_cannot_make_wait_unbounded(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(watchdog.time, 'time', return_value=1000):
            path = Path(folder) / 'cooldown.json'
            for value in ['broken', 'null', '{"retry_at":true,"delay_seconds":300}', '{"retry_at":NaN,"delay_seconds":300}']:
                path.write_text(value, encoding='utf-8')
                self.assertEqual(watchdog.RateLimitCooldown(path).remaining(), 0)
            path.write_text('{"retry_at":1000000,"delay_seconds":300}', encoding='utf-8')
            self.assertEqual(watchdog.RateLimitCooldown(path).remaining(), 1800)

    def test_only_provisioning_rate_limit_logs_trigger_cooldown(self):
        self.assertTrue(watchdog.RATE_LIMIT_RE.search('quick tunnel provisioning failed with status 429'))
        self.assertTrue(watchdog.RATE_LIMIT_RE.search('failed to request quick Tunnel: status code 429'))
        for line in ['quick tunnel provisioning failed with status 500', 'api heartbeat status 429', 'Created Connector ID 429', 'TLS handshake: EOF']:
            self.assertIsNone(watchdog.RATE_LIMIT_RE.search(line))

    def test_actual_429_loop_keeps_cooldown_across_three_manual_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / 'fake-cloudflared.exe'
            executable.touch()
            kernel = MagicMock()
            kernel.AssignProcessToJobObject.return_value = True
            registry = MagicMock()
            registry.QueryValueEx.side_effect = OSError()
            clock = [100.0]
            stop_at = [400.0]
            launches = []
            statuses = []
            def spawn(*args, **kwargs):
                launches.append(clock[0])
                child = MagicMock(pid=1234 + len(launches))
                child.poll.return_value = 1
                child._handle = 1
                child.stdout = io.StringIO('quick tunnel provisioning failed with status 429\n')
                return child
            def thread_factory(**kwargs):
                thread = MagicMock()
                thread.start.side_effect = lambda: kwargs['target'](*kwargs['args'])
                return thread
            original_write = watchdog.write_json
            def write(path, data):
                if path.name == 'tunnel-status.json':
                    statuses.append(data)
                original_write(path, data)
            args = argparse.Namespace(executable=str(executable), origin='http://127.0.0.1:8765', metrics_port=20246)
            def sleep(seconds):
                clock[0] += seconds
                if clock[0] >= stop_at[0]:
                    raise KeyboardInterrupt()
            with patch.object(watchdog, 'ROOT', root), patch.object(watchdog.Path, 'home', return_value=root), \
                    patch.dict(sys.modules, {'winreg':registry}), \
                    patch.object(watchdog, 'create_child_job', return_value=(kernel,10)), \
                    patch.object(watchdog.socket, 'socket'), \
                    patch.object(watchdog.subprocess, 'Popen', side_effect=spawn), \
                    patch.object(watchdog.threading, 'Thread', side_effect=thread_factory), \
                    patch.object(watchdog.time, 'monotonic', side_effect=lambda:clock[0]), \
                    patch.object(watchdog.time, 'time', side_effect=lambda:900 + clock[0]), \
                    patch.object(watchdog.time, 'sleep', side_effect=sleep), \
                    patch.object(watchdog, 'write_json', side_effect=write), \
                    patch.object(watchdog, 'stop_child'), patch.object(watchdog.logging, 'basicConfig'), \
                    patch.object(watchdog.logging, 'warning'):
                for _ in range(3):
                    with self.assertRaises(KeyboardInterrupt):
                        watchdog.run_watchdog(args)
                    stop_at[0] += 300
            self.assertEqual(launches, [100,400])
            self.assertEqual(clock[0], 1000)
            saved = json.loads((root / 'logs/tunnel-rate-limit.json').read_text())
            self.assertEqual(saved['delay_seconds'], 600)
            self.assertEqual(saved['retry_at'], 1900)
            self.assertTrue(any(s['state']=='启动重试中' and s['rate_limit_remaining']>=300 for s in statuses))
            self.assertEqual(kernel.CloseHandle.call_count, 3)

    def test_provisioning_api_in_user_timeout_log_is_not_a_tunnel_address(self):
        line = 'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": context deadline exceeded (Client.Timeout exceeded while awaiting headers)'
        self.assertIsNone(watchdog.URL_RE.search(line))
        for url in ['https://api.trycloudflare.com', 'https://cat.trycloudflare.com.evil.test', 'https://cat.trycloudflare.com:443', 'https://-cat.trycloudflare.com']:
            self.assertIsNone(watchdog.URL_RE.search(url))

    def test_created_tunnel_url_in_cloudflared_table_is_recognized(self):
        line = '2026-10-06T03:00:00Z INF |  https://sample-public-tunnel.trycloudflare.com  |'
        self.assertEqual(watchdog.URL_RE.search(line).group(0), 'https://sample-public-tunnel.trycloudflare.com')

    def test_metrics_with_and_without_labels(self):
        self.assertEqual(watchdog.active_connections(
            '# HELP example\ncloudflared_tunnel_ha_connections 1\n'), 1)
        self.assertEqual(watchdog.active_connections(
            'cloudflared_tunnel_ha_connections{a="b"} 1\n'
            'cloudflared_tunnel_ha_connections{a="c"} 2\n'), 3)
        self.assertEqual(watchdog.active_connections('cloudflared_tunnel_ha_connections 0\n'), 0)
        for metrics in ['# no sample', 'html error', 'cloudflared_tunnel_ha_connections -1\n',
                        'cloudflared_tunnel_ha_connections 1e309\n']:
            self.assertIsNone(watchdog.active_connections(metrics))

    def test_startup_grace(self):
        policy = watchdog.ConnectionPolicy(100)
        self.assertFalse(policy.observe(279.9, False))
        self.assertTrue(policy.observe(280, False))

    def test_disconnect_restarts_after_six_seconds(self):
        policy = watchdog.ConnectionPolicy(0, disconnect_seconds=6)
        self.assertFalse(policy.observe(1, True))
        self.assertFalse(policy.observe(2, False))
        self.assertFalse(policy.observe(7.9, False))
        self.assertTrue(policy.observe(8, False))

    def test_brief_disconnect_recovers_without_restart(self):
        policy = watchdog.ConnectionPolicy(0)
        policy.observe(1, True)
        policy.observe(2, False)
        self.assertFalse(policy.observe(7, True))
        self.assertFalse(policy.observe(8, False))
        self.assertFalse(policy.observe(13, False))

    def test_outage_under_thirty_seconds_does_not_change_address(self):
        policy = watchdog.ConnectionPolicy(0)
        policy.observe(1, True)
        self.assertFalse(policy.observe(2, False))
        self.assertFalse(policy.observe(31.9, False))
        self.assertFalse(policy.observe(31.95, True))

    def test_outage_restarts_at_thirty_seconds(self):
        policy = watchdog.ConnectionPolicy(0)
        policy.observe(1, True)
        policy.observe(2, False)
        self.assertFalse(policy.observe(31.9, False))
        self.assertTrue(policy.observe(32, False))

    def test_unknown_connection_cannot_trigger_startup_or_disconnect_restart(self):
        policy = watchdog.ConnectionPolicy(0)
        self.assertFalse(policy.observe(1000, None))
        policy.observe(1001, True)
        policy.observe(1002, False)
        self.assertFalse(policy.observe(1040, None))
        self.assertFalse(policy.observe(1041, False))
        self.assertFalse(policy.observe(1070.9, False))
        self.assertTrue(policy.observe(1071, False))

    def test_missing_heartbeats_become_diagnostic_after_thirty_seconds(self):
        policy = watchdog.HeartbeatPolicy(100)
        self.assertFalse(policy.observe(129.9))
        self.assertTrue(policy.observe(130))

    def test_active_packet_keeps_heartbeat_healthy_without_any_browser(self):
        policy = watchdog.HeartbeatPolicy(0)
        for now in range(0, 301, 5):
            self.assertFalse(policy.observe(now, probe_ok=True))
            self.assertTrue(policy.healthy(now))

    def test_browser_packet_keeps_heartbeat_recent_when_active_probe_fails(self):
        policy = watchdog.HeartbeatPolicy(0)
        self.assertFalse(policy.observe(29, browser_age=1))
        self.assertFalse(policy.observe(57.9))
        self.assertTrue(policy.observe(58))

    def test_stale_browser_packet_does_not_extend_deadline(self):
        policy = watchdog.HeartbeatPolicy(0)
        policy.observe(10, browser_age=0)
        for now in (12, 18, 25, 30, 39.9):
            self.assertFalse(policy.observe(now, browser_age=now - 10))
        self.assertTrue(policy.observe(40, browser_age=30))

    def test_invalid_browser_age_cannot_keep_tunnel_alive(self):
        for age in (True, -1, float('inf'), float('nan'), '0', None):
            self.assertTrue(watchdog.HeartbeatPolicy(0).observe(30, browser_age=age))

    def test_public_probe_checks_nonce_and_server_instance(self):
        def reply(url, timeout):
            nonce = parse_qs(urlsplit(url).query)['nonce'][0]
            return json.dumps({'status':'ok','server_id':'worker-1','nonce':nonce})
        with patch.object(watchdog, 'get_text', side_effect=reply):
            self.assertTrue(watchdog.probe_public('https://test.trycloudflare.com', 'worker-1')[0])
            self.assertFalse(watchdog.probe_public('https://test.trycloudflare.com', 'other-worker')[0])
        with patch.object(watchdog, 'get_text', return_value='{"status":"ok","server_id":"worker-1","nonce":"cached"}'):
            self.assertFalse(watchdog.probe_public('https://test.trycloudflare.com', 'worker-1')[0])

    def test_cloudflare_1016_and_1033_are_failed_packets(self):
        for code in (1016, 1033):
            error = HTTPError('https://example', 530, 'error', {}, io.BytesIO(f'error code: {code}'.encode()))
            with patch.object(watchdog, 'get_text', side_effect=error):
                ok, message = watchdog.probe_public('https://example', 'worker-1')
                self.assertFalse(ok)
                self.assertIn(str(code), message)

    def test_error_body_read_timeout_is_caught(self):
        body = MagicMock()
        body.read.side_effect = TimeoutError('The read operation timed out')
        error = HTTPError('https://example', 530, 'error', {}, body)
        with patch.object(watchdog, 'get_text', side_effect=error):
            self.assertFalse(watchdog.probe_public('https://example', 'worker-1')[0])
        body.close.assert_called_once()

    def test_bad_json_and_network_timeout_are_failed_packets(self):
        for error in (TimeoutError(), watchdog.HTTPException('truncated response')):
            with patch.object(watchdog, 'get_text', side_effect=error):
                self.assertFalse(watchdog.probe_public('https://example', 'worker-1')[0])
        for data in ('html', 'null', '[]', '{}'):
            with patch.object(watchdog, 'get_text', return_value=data):
                self.assertFalse(watchdog.probe_public('https://example', 'worker-1')[0])

    def test_json_replace_retries_and_preserves_atomic_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            path.write_text('{"old":true}')
            real_replace = Path.replace
            calls = []
            def replace(temporary, target):
                calls.append(temporary)
                self.assertEqual(json.loads(path.read_text()), {'old': True})
                if len(calls) < 3:
                    raise PermissionError('temporary reader lock')
                return real_replace(temporary, target)
            with patch.object(Path, 'replace', new=replace), patch.object(watchdog.time, 'sleep'):
                watchdog.write_json(path, {'new': True})
            self.assertEqual(len(calls), 3)
            self.assertEqual(json.loads(path.read_text()), {'new': True})
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_persistent_json_lock_is_bounded_and_leaves_old_status(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            path.write_text('{"old":true}')
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')) as replace, patch.object(watchdog.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    watchdog.write_json(path, {'new': True})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(json.loads(path.read_text()), {'old': True})
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows file sharing test')
    def test_real_windows_reader_lock_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            path.write_text('{"old":true}')
            reader = path.open('r')
            timer = threading.Timer(0.15, reader.close)
            timer.start()
            try:
                watchdog.write_json(path, {'new': True})
                self.assertEqual(json.loads(path.read_text()), {'new': True})
            finally:
                reader.close()
                timer.join()

    def test_origin_health(self):
        for body, expected in [(' {"status":"ok"}', True), ('{"status":"error"}', False), ('html', False)]:
            with patch.object(watchdog, "get_text", return_value=body):
                self.assertEqual(watchdog.local_health("http://127.0.0.1:8765"), expected)

    def simulate_running_watchdog(self, browser_age, connections, periodic=False, registered=True,
                                  protocol=None, end_at=165, local_ready=True,
                                  metric_error=False, public_ok=False):
        """Exercise the actual supervisor loop with no processes or sockets."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / 'fake-cloudflared.exe'
            executable.touch()
            child = MagicMock(pid=1234)
            child.poll.return_value = None
            registered_line = '2026-10-06T03:00:00Z INF Registered tunnel connection connIndex=0 protocol=quic\n' if registered and connections else ''
            child.stdout = io.StringIO('URL https://current.trycloudflare.com\n' + registered_line)
            child._handle = 1
            kernel = MagicMock()
            kernel.AssignProcessToJobObject.return_value = True
            registry = MagicMock()
            registry.QueryValueEx.side_effect = OSError()
            clock = [100.0]
            statuses = []
            def metrics_reply(*args, **kwargs):
                if metric_error:
                    raise TimeoutError('metrics read timed out')
                value = connections(clock[0]) if callable(connections) else connections
                return '# no sample' if value is None else f'cloudflared_tunnel_ha_connections {value}\n'
            def sleep(seconds):
                clock[0] += seconds
                if clock[0] >= (460 if periodic else end_at):
                    raise KeyboardInterrupt()
            def thread_factory(**kwargs):
                thread = MagicMock()
                thread.start.side_effect = lambda: kwargs['target'](*kwargs['args'])
                return thread
            args = argparse.Namespace(executable=str(executable), origin='http://127.0.0.1:8765', metrics_port=20246)
            if periodic:
                args.self_restart_seconds = 300
            if protocol is not None:
                args.protocol = protocol
            with patch.object(watchdog, 'ROOT', root), patch.object(watchdog.Path, 'home', return_value=root), \
                    patch.dict(sys.modules, {'winreg': registry}), \
                    patch.object(watchdog, 'create_child_job', return_value=(kernel, 10)), \
                    patch.object(watchdog.socket, 'socket'), \
                    patch.object(watchdog.subprocess, 'Popen', side_effect=[child, KeyboardInterrupt()]) as launch, \
                    patch.object(watchdog.threading, 'Thread', side_effect=thread_factory), \
                    patch.object(watchdog.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(watchdog.time, 'sleep', side_effect=sleep), \
                    patch.object(watchdog, 'get_text', side_effect=metrics_reply), \
                    patch.object(watchdog, 'local_heartbeat', return_value={'server_id':'worker-1', 'browser_heartbeat_age':browser_age} if local_ready else None), \
                    patch.object(watchdog, 'probe_public', return_value=(public_ok, '公网心跳正常' if public_ok else 'Cloudflare 1016')), \
                    patch.object(watchdog, 'write_json', side_effect=lambda path, data: statuses.append(data)), \
                    patch.object(watchdog, 'stop_child') as stop, patch.object(watchdog.logging, 'basicConfig'):
                if periodic:
                    self.assertTrue(watchdog.run_watchdog(args))
                    self.assertEqual(clock[0], 400)
                    stop.assert_called_once_with(child)
                    kernel.CloseHandle.assert_called_once_with(10)
                else:
                    with self.assertRaises(KeyboardInterrupt):
                        watchdog.run_watchdog(args)
                command = launch.call_args_list[0].args[0]
                self.assertEqual(command[command.index('--protocol') + 1], protocol or 'auto')
                return launch.call_count, statuses

    def test_actual_loop_retains_connected_tunnel_despite_missing_heartbeats(self):
        launches, statuses = self.simulate_running_watchdog(None, 1, end_at=1060)
        self.assertEqual(launches, 1)
        self.assertFalse(any(state['state'] == '可访问' for state in statuses))
        self.assertFalse(any(state['state'] == '重启中' for state in statuses))
        self.assertTrue(any('30 秒无成功心跳' in state['detail'] and '隧道连接仍在' in state['detail'] for state in statuses))

    def test_actual_loop_restarts_only_after_confirmed_continuous_disconnect(self):
        launches, statuses = self.simulate_running_watchdog(None, lambda now: 1 if now < 105 else 0)
        self.assertEqual(launches, 2)
        self.assertTrue(any(s['state'] == '重启中' and '连接持续中断 30 秒' in s['detail'] for s in statuses))

    def test_actual_loop_brief_disconnect_keeps_original_tunnel(self):
        launches, statuses = self.simulate_running_watchdog(None, lambda now: 0 if 105 <= now < 125 else 1)
        self.assertEqual(launches, 1)
        self.assertFalse(any(s['state'] == '重启中' for s in statuses))

    def test_actual_loop_metric_timeout_is_unknown_not_disconnected(self):
        launches, statuses = self.simulate_running_watchdog(None, 1, metric_error=True, end_at=400)
        self.assertEqual(launches, 1)
        self.assertFalse(any(s['state'] == '重启中' for s in statuses))
        self.assertTrue(any('状态待确认' in s['detail'] and s['connection_confirmed'] is None for s in statuses))

    def test_actual_loop_missing_metric_after_connection_keeps_original_tunnel(self):
        launches, statuses = self.simulate_running_watchdog(None, lambda now: 1 if now < 105 else None, end_at=400)
        self.assertEqual(launches, 1)
        self.assertFalse(any(s['state'] == '重启中' for s in statuses))
        self.assertTrue(any('状态待确认' in s['detail'] and s['metrics_connections'] is None for s in statuses))

    def test_actual_loop_local_service_failure_does_not_rebuild_connected_tunnel(self):
        launches, statuses = self.simulate_running_watchdog(None, 1, local_ready=False, end_at=400)
        self.assertEqual(launches, 1)
        self.assertFalse(any(s['state'] == '重启中' for s in statuses))
        self.assertTrue(any(s['state'] == '本地服务未就绪' and s['local_service_ready'] is False for s in statuses))

    def test_actual_loop_active_public_packet_prevents_false_disconnect(self):
        launches, statuses = self.simulate_running_watchdog(None, 0, public_ok=True, end_at=400)
        self.assertEqual(launches, 1)
        self.assertFalse(any(s['state'] == '重启中' for s in statuses))
        self.assertTrue(any(s['state'] == '可访问' for s in statuses))

    def test_actual_loop_browser_packets_override_bad_metrics_and_probe(self):
        launches, statuses = self.simulate_running_watchdog(0, 0)
        self.assertEqual(launches, 1)
        self.assertTrue(any(state['state'] == '可访问' for state in statuses))
        self.assertFalse(any(state['state'] == '重启中' for state in statuses))

    def test_first_connection_keeps_startup_grace_without_public_packets(self):
        launches, statuses = self.simulate_running_watchdog(None, 0)
        self.assertEqual(launches, 1)
        self.assertFalse(any(state['state'] == '重启中' for state in statuses))
        launches, statuses = self.simulate_running_watchdog(None, 0, end_at=400)
        self.assertEqual(launches, 2)
        self.assertTrue(any(s['state'] == '重启中' and '180 秒内未建立隧道连接' in s['detail'] for s in statuses))

    def test_unregistered_handshake_metric_does_not_trigger_thirty_second_restart(self):
        launches, statuses = self.simulate_running_watchdog(None, 1, registered=False, end_at=400)
        self.assertEqual(launches, 1)
        self.assertFalse(any(state['state'] == '重启中' for state in statuses))
        self.assertTrue(all(not state['registration_confirmed'] for state in statuses))

    def test_explicit_protocol_is_used_by_actual_supervisor(self):
        for protocol in ['quic', 'http2']:
            launches, statuses = self.simulate_running_watchdog(0, 1, protocol=protocol)
            self.assertEqual(launches, 1)
            self.assertTrue(all(state['protocol'] == protocol for state in statuses))

    def test_public_probe_uses_configured_proxy_while_local_probes_bypass_it(self):
        for url, public in [('http://127.0.0.1:8765/health',False),('http://localhost/health',False),('https://test.trycloudflare.com/health',True)]:
            with patch.object(watchdog, 'DIRECT_HTTP') as direct, patch.object(watchdog, 'PUBLIC_HTTP') as remote:
                expected = remote if public else direct
                expected.open.return_value.__enter__.return_value.read.return_value = b'ok'
                self.assertEqual(watchdog.get_text(url), 'ok')
                expected.open.assert_called_once()
                (direct if public else remote).open.assert_not_called()

    def test_healthy_watchdog_keeps_same_process_and_tunnel_for_fifteen_minutes(self):
        launches, statuses = self.simulate_running_watchdog(0, 1, end_at=1060)
        self.assertEqual(launches, 1)
        self.assertFalse(any(s['state'] == '重启中' for s in statuses))
        self.assertTrue(all(s['self_restart_seconds'] == 0 and s['self_restart_remaining'] is None for s in statuses))

    def test_explicit_periodic_setting_is_available_but_not_the_default(self):
        launches, statuses = self.simulate_running_watchdog(0, 1, periodic=True)
        self.assertEqual(launches, 1)
        self.assertEqual(statuses[-1]['state'], '重启中')
        self.assertIn('定时重启看门狗', statuses[-1]['detail'])
        self.assertEqual(statuses[-1]['self_restart_remaining'], 0)
        self.assertEqual(statuses[-1]['watchdog_version'], 10)

    def test_restart_timer_deadline_and_disabled_setting(self):
        timer = watchdog.RestartTimer(100, 300)
        self.assertFalse(timer.due(399.99))
        self.assertTrue(timer.due(400))
        self.assertEqual(timer.remaining(399), 1)
        disabled = watchdog.RestartTimer(100, 0)
        self.assertFalse(disabled.due(100000))
        self.assertIsNone(disabled.remaining(100000))
        with patch.object(watchdog.time, 'monotonic', return_value=399), patch.object(watchdog.time, 'sleep') as sleep:
            timer.pause(30)
            sleep.assert_called_once_with(1)

    def test_failed_launch_backoff_cannot_delay_five_minute_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / 'fake-cloudflared.exe'
            executable.touch()
            kernel = MagicMock()
            registry = MagicMock()
            registry.QueryValueEx.side_effect = OSError()
            clock = [100.0]
            args = argparse.Namespace(executable=str(executable), origin='http://127.0.0.1:8765', metrics_port=20246)
            args.self_restart_seconds = 300
            with patch.object(watchdog, 'ROOT', root), patch.object(watchdog.Path, 'home', return_value=root), \
                    patch.dict(sys.modules, {'winreg': registry}), \
                    patch.object(watchdog, 'create_child_job', return_value=(kernel, 10)), \
                    patch.object(watchdog.socket, 'socket'), \
                    patch.object(watchdog.subprocess, 'Popen', side_effect=OSError('launch unavailable')), \
                    patch.object(watchdog.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(watchdog.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                    patch.object(watchdog.logging, 'basicConfig'), patch.object(watchdog.logging, 'error'), \
                    patch.object(watchdog, 'write_json'):
                self.assertTrue(watchdog.run_watchdog(args))
                self.assertEqual(clock[0], 400)
                kernel.CloseHandle.assert_called_once_with(10)

    def test_main_releases_mutex_before_starting_replacement(self):
        events = []
        kernel = MagicMock()
        kernel.CloseHandle.side_effect = lambda handle: events.append('mutex closed')
        def stopped(args):
            self.assertEqual(args.self_restart_seconds, 300)
            self.assertEqual(args.protocol, 'auto')
            events.append('old child job closed')
            return True
        def relaunched():
            events.append('new watchdog launched')
            return 4321
        with patch.object(watchdog.sys, 'argv', ['watchdog.py', '--self-restart-seconds', '300']), \
                patch.object(watchdog, 'require_vm'), \
                patch.object(watchdog, 'acquire_mutex', return_value=(kernel, 10)), \
                patch.object(watchdog, 'run_watchdog', side_effect=stopped), \
                patch.object(watchdog, 'relaunch_watchdog', side_effect=relaunched):
            self.assertEqual(watchdog.run_program(), 4321)
        self.assertEqual(events, ['old child job closed', 'mutex closed', 'new watchdog launched'])

    def test_default_command_line_disables_periodic_restart(self):
        kernel = MagicMock()
        def stopped(args):
            self.assertEqual(args.self_restart_seconds, 0)
            return None
        with patch.object(watchdog.sys, 'argv', ['watchdog.py']), \
                patch.object(watchdog, 'require_vm'), \
                patch.object(watchdog, 'acquire_mutex', return_value=(kernel,10)), \
                patch.object(watchdog, 'run_watchdog', side_effect=stopped), \
                patch.object(watchdog, 'relaunch_watchdog') as relaunch:
            watchdog.run_program()
        relaunch.assert_not_called()

    def test_manual_interrupt_does_not_launch_replacement(self):
        with patch.object(watchdog, 'main', side_effect=KeyboardInterrupt()), \
                patch.object(watchdog, 'relaunch_watchdog') as relaunch:
            with self.assertRaises(KeyboardInterrupt):
                watchdog.run_program()
            relaunch.assert_not_called()

    def test_relaunch_preserves_python_script_arguments_and_working_directory(self):
        with patch.object(watchdog.sys, 'argv', ['watchdog.py', '--self-restart-seconds', '300']), \
                patch.object(watchdog.time, 'sleep'), \
                patch.object(watchdog.subprocess, 'Popen', return_value=MagicMock(pid=4321)) as launch:
            self.assertEqual(watchdog.relaunch_watchdog(), 4321)
            launch.assert_called_once_with([sys.executable, str(SOURCE.resolve()), '--self-restart-seconds', '300'], cwd=str(watchdog.ROOT))

    def test_only_own_child_terminated(self):
        child = MagicMock()
        child.poll.return_value = None
        watchdog.stop_child(child)
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()

    def test_hung_child_is_killed(self):
        child = MagicMock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("fake", 5), 0]
        watchdog.stop_child(child)
        child.kill.assert_called_once_with()

    def test_process_exit_restarts_and_clears_old_address(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "fake-cloudflared.exe"
            executable.touch()  # Empty file; Popen below is mocked, never executed.
            child = MagicMock()
            child.pid = 1234
            child.poll.return_value = 1
            child.stdout = io.StringIO('failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": context deadline exceeded\nURL https://expired.trycloudflare.com\n')
            child._handle = 1
            kernel = MagicMock()
            kernel.AssignProcessToJobObject.return_value = True
            registry = MagicMock()
            registry.QueryValueEx.side_effect = OSError("No redirected desktop in test")
            args = argparse.Namespace(executable=str(executable), origin="http://127.0.0.1:8765", metrics_port=20246)
            def thread_factory(**kwargs):
                thread = MagicMock()
                thread.start.side_effect = lambda: kwargs['target'](*kwargs['args'])
                return thread
            with patch.object(watchdog, "ROOT", root), \
                    patch.object(watchdog.Path, "home", return_value=root), \
                    patch.dict(sys.modules, {"winreg": registry}), \
                    patch.object(watchdog, "create_child_job", return_value=(kernel, 10)), \
                    patch.object(watchdog.socket, "socket") as socket_factory, \
                    patch.object(watchdog.subprocess, "Popen", side_effect=[child, KeyboardInterrupt()]) as launch, \
                    patch.object(watchdog.threading, "Thread", side_effect=thread_factory), \
                    patch.object(watchdog.time, "sleep") as sleep, \
                    patch.object(watchdog.logging, "basicConfig"), patch.object(watchdog.logging, "info") as info:
                with self.assertRaises(KeyboardInterrupt):
                    watchdog.run_watchdog(args)
                self.assertEqual(launch.call_count, 2)
                sleep.assert_any_call(1)
                command = launch.call_args_list[0].args[0]
                self.assertEqual(command[command.index('--protocol') + 1], 'auto')
                self.assertIn("127.0.0.1:20246", command)
                self.assertNotIn("trycloudflare.com", (root / "公网地址.txt").read_text(encoding="utf-8-sig"))
                self.assertFalse((root / 'logs/tunnel-url-history.jsonl').exists())
                self.assertTrue(any('failed to request quick Tunnel' in str(call) for call in info.call_args_list))
                self.assertFalse(any(call.args and call.args[0] == '新的公网地址：%s' for call in info.call_args_list))
                kernel.CloseHandle.assert_called_once_with(10)

    def test_host_is_rejected(self):
        registry = MagicMock()
        registry.QueryValueEx.return_value = ("Physical Desktop", 1)
        with patch.dict(sys.modules, {"winreg": registry}), patch.object(watchdog.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "虚拟机"):
                watchdog.require_vm()

    def test_failed_launch_retries_without_exiting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "fake-cloudflared.exe"
            executable.touch()
            kernel = MagicMock()
            registry = MagicMock()
            registry.QueryValueEx.side_effect = OSError()
            args = argparse.Namespace(executable=str(executable), origin="http://127.0.0.1:8765", metrics_port=20246)
            with patch.object(watchdog, "ROOT", root), \
                    patch.object(watchdog.Path, "home", return_value=root), \
                    patch.dict(sys.modules, {"winreg": registry}), \
                    patch.object(watchdog, "create_child_job", return_value=(kernel, 10)), \
                    patch.object(watchdog.socket, "socket"), \
                    patch.object(watchdog.subprocess, "Popen", side_effect=[OSError("temporary launch failure"), KeyboardInterrupt()]) as launch, \
                    patch.object(watchdog.time, "sleep") as sleep, \
                    patch.object(watchdog, "write_json", side_effect=OSError("status file locked")), \
                    patch.object(watchdog.logging, "basicConfig"):
                with self.assertRaises(KeyboardInterrupt):
                    watchdog.run_watchdog(args)
                self.assertEqual(launch.call_count, 2)
                sleep.assert_any_call(1)
                kernel.CloseHandle.assert_called_once_with(10)

    @unittest.skipUnless(sys.platform == "win32", "Windows kernel test")
    def test_windows_child_cleanup_job_can_be_created(self):
        kernel, handle = watchdog.create_child_job()
        self.assertTrue(handle)
        kernel.CloseHandle(handle)

    @unittest.skipUnless(sys.platform == "win32", "Windows kernel test")
    def test_duplicate_watchdog_mutex_is_rejected(self):
        kernel, handle = watchdog.acquire_mutex()
        try:
            with self.assertRaisesRegex(RuntimeError, "已经在运行"):
                watchdog.acquire_mutex()
        finally:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
