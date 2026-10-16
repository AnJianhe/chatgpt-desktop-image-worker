"""Outbound Sites bridge. Desktop operations remain in the existing serial server."""
import contextlib
import ctypes
import hashlib
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

BASE = Path(__file__).resolve().parent
TERMINAL = {'done', 'error', 'not_generated'}


class HTTP:
    def __init__(self, origin, token=None, local=False, timeout=30):
        self.origin = origin.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()

    def call(self, path, data=None, method=None, raw=False, content_type=None):
        headers = {'User-Agent': 'Mozilla/5.0'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if content_type:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(self.origin + path, data=data, headers=headers, method=method)
        with self.opener.open(req, timeout=self.timeout) as response:
            payload = response.read()
            return (payload, response.headers.get_content_type()) if raw else json.loads(payload)


def kuma_status(root=BASE.parent / 'uptime-kuma'):
    path = root / 'data' / 'kuma.db'
    if not path.is_file():
        return {'monitors': [], 'error': 'Uptime Kuma 尚未安装或尚未初始化'}
    try:
        with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT m.name,h.status,h.msg,h.time FROM monitor m LEFT JOIN heartbeat h ON h.id=(SELECT id FROM heartbeat WHERE monitor_id=m.id ORDER BY id DESC LIMIT 1) WHERE m.active=1 ORDER BY m.id').fetchall()
            monitors = [dict(row) for row in rows]
            for monitor in monitors:
                try:
                    timestamp = datetime.fromisoformat(monitor['time']).replace(tzinfo=timezone.utc).timestamp()
                    if time.time() - timestamp > 120:
                        monitor.update(status=None, msg='监控采样已过期，请检查 Uptime Kuma 是否运行')
                except (TypeError, ValueError):
                    monitor.update(status=None, msg='尚未收到监控采样')
            return {'monitors': monitors}
    except (OSError, sqlite3.Error) as exc:
        return {'monitors': [], 'error': '监控数据库暂不可读取：' + type(exc).__name__}


class Agent:
    def __init__(self, config, state_path=BASE / 'logs' / 'cloud-agent-state.json'):
        self.cloud = HTTP(config['endpoint'], config['worker_token'])
        self.local = HTTP(config.get('local_url', 'http://127.0.0.1:8765'), local=True)
        self.state_path = Path(state_path)
        try:
            self.state = json.loads(self.state_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            self.state = {}
        self.last_heartbeat = 0
        self.background_heartbeat = False

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state), encoding='utf-8')
        os.replace(tmp, self.state_path)

    def upload_reference(self, payload, mime, upload_id=None):
        boundary = uuid.uuid4().hex
        upload_id = upload_id or uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="upload_id"\r\n\r\n{upload_id}\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="reference.png"\r\nContent-Type: {mime}\r\n\r\n'.encode() + payload + f'\r\n--{boundary}--\r\n'.encode())
        return self.local.call('/uploads', body, content_type='multipart/form-data; boundary=' + boundary)['upload_id']

    def heartbeat_loop(self, stop):
        # Separate HTTP clients: slow references/results must not delay liveness.
        cloud = HTTP(self.cloud.origin, self.cloud.token, timeout=10)
        local = HTTP(self.local.origin, local=True, timeout=3)
        while not stop.is_set():
            try:
                try:
                    health = local.call('/health')
                except Exception:
                    health = {'status': 'offline', 'worker_alive': False}
                cloud.call('/worker/heartbeat', {'local_health': health, 'kuma': kuma_status(), 'version': 'cloud-1.0.4'})
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.with_name('cloud-status.json').write_text(json.dumps({'updated':time.time(),'online':True,'worker_alive':health.get('worker_alive',False)}),encoding='utf-8')
            except Exception as exc:
                logging.warning('心跳同步暂时中断（%s），稍后重试', type(exc).__name__)
            stop.wait(5)

    def step(self):
        try:
            health = self.local.call('/health')
        except Exception:
            health = {'status': 'offline', 'worker_alive': False}
        if not self.background_heartbeat and time.monotonic() - self.last_heartbeat >= 5:
            self.cloud.call('/worker/heartbeat', {'local_health': health, 'kuma': kuma_status(), 'version': 'cloud-1.0.1'})
            self.last_heartbeat = time.monotonic()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.with_name('cloud-status.json').write_text(json.dumps({'updated':time.time(),'online':True,'worker_alive':health.get('worker_alive',False)}),encoding='utf-8')
        if not health.get('worker_alive'):
            return
        task = self.cloud.call('/worker/claim', {}, 'POST')['task']
        if not task:
            return
        task_id = task['id']
        if self.state.get('task_id') != task_id:
            self.state = {'task_id': task_id, 'uploads': [], 'local_id': task.get('local_id')}
            self.save()
        local_id = self.state.get('local_id') or task.get('local_id')
        if not local_id:
            for ref in task['upload_ids'][len(self.state['uploads']):]:
                data, mime = self.cloud.call('/worker/uploads/' + ref, raw=True)
                self.state['uploads'].append(self.upload_reference(data, mime, ref))
                self.save()
            job = self.local.call('/jobs', {'prompt': task['prompt'], 'tool': task['tool'], 'upload_ids': list(self.state['uploads']), 'request_id': task_id})
            local_id = job['id']
            self.state['local_id'] = local_id
            self.save()
        self.sync(task_id, local_id)

    def sync(self, task_id, local_id):
        prefix = '/worker/tasks/' + task_id
        # Local review keeps detecting in the background. A stale decision can never
        # undo a completed download or restart an ended task.
        decision = self.cloud.call(prefix + '/decision')['decision']
        if decision:
            try:
                self.local.call('/jobs/' + local_id + '/review', decision)
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
            self.cloud.call(prefix + '/decision', decision)
        try:
            snap = self.local.call('/jobs/' + local_id)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            snap = {'status': 'error', 'error': '本机任务记录不存在；为避免重复生成，请重新提交新任务。'}
        status = snap['status']
        if status == 'queued':
            # Cloud claim owns the desktop slot even while the local server finishes
            # an older local job. Do not release it by reporting cloud queued.
            snap['status'] = 'running'
        if status in ('done', 'review'):
            version = snap.get('review_version', 0)
            key = 'done' if status == 'done' else 'preview-' + str(version)
            if self.state.get('uploaded') != key:
                endpoint = '/image' if status == 'done' else '/preview'
                data, mime = self.local.call('/jobs/' + local_id + endpoint, raw=True)
                target = '/result' if status == 'done' else '/preview?v=' + str(version)
                self.cloud.call(prefix + target, data, 'PUT', content_type=mime)
                self.state['uploaded'] = key
                self.save()
        snap['local_id'] = local_id
        try:
            logs, mime = self.local.call('/jobs/' + local_id + '/log', raw=True)
            digest = hashlib.sha256(logs).hexdigest()
            if self.state.get('log_digest') != digest:
                self.cloud.call(prefix + '/log', logs, 'PUT', content_type='application/x-ndjson')
                self.state['log_digest'] = digest
                self.save()
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        self.cloud.call(prefix + '/snapshot', snap)
        if status in TERMINAL:
            logging.info('任务 %s 已同步：%s', task_id, status)
            self.state = {}
            self.save()


def main():
    # A single VM must never run two bridges driving the same desktop.
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel.CreateMutexW(None, False, 'Local\\ChatGPTSitesCloudAgent')
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            return
    logs = BASE / 'logs'
    logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=[RotatingFileHandler(logs / 'cloud-agent.log', maxBytes=2*1024*1024, backupCount=3, encoding='utf-8')])
    config = json.loads((BASE / 'cloud_agent_config.json').read_text(encoding='utf-8-sig'))
    if not config['endpoint'].startswith('https://') or not config.get('worker_token'):
        raise ValueError('请配置 HTTPS 云端地址和 worker_token')
    agent = Agent(config)
    agent.background_heartbeat = True
    stop = threading.Event()
    threading.Thread(target=agent.heartbeat_loop, args=(stop,), daemon=True, name='sites-heartbeat').start()
    logging.info('云端任务同步已启动')
    while True:
        try:
            agent.step()
        except urllib.error.HTTPError as exc:
            # Per-task local validation failures must not block later cloud jobs.
            if exc.code in (400, 422) and exc.url.startswith(agent.local.origin) and agent.state.get('task_id'):
                try:
                    agent.cloud.call('/worker/tasks/' + agent.state['task_id'] + '/snapshot', {'status': 'error', 'error': '本机拒绝任务参数，请检查提示词和参考图片。'})
                    agent.state = {}
                    agent.save()
                except Exception:
                    logging.warning('任务错误状态同步失败，将重试')
            else:
                logging.warning('同步响应 %s，保留任务并重试', exc.code)
        except Exception as exc:
            # Never log request/config secrets; preserve local_id across outages.
            logging.warning('同步暂时中断（%s），保留任务并重试', type(exc).__name__)
        time.sleep(2)


if __name__ == '__main__':
    main()
