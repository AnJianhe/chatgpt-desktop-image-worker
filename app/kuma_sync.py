"""Read-only Kuma statistics publisher, independent of image task processing."""
import contextlib
import ctypes
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import time

from cloud_agent import HTTP

BASE = Path(__file__).resolve().parent
WINDOWS = {'24h': ('stat_minutely', 86400, 900),
           '30d': ('stat_hourly', 30 * 86400, 8 * 3600),
           '1y': ('stat_daily', 365 * 86400, 4 * 86400)}


def incident_history(db, monitor_id, now):
    since=datetime.fromtimestamp(now-7*86400,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    rows=db.execute('SELECT status,time,msg FROM heartbeat WHERE monitor_id=? AND time>=? ORDER BY id DESC LIMIT 25000',(monitor_id,since)).fetchall()
    incidents=[];current=None
    for row in reversed(rows):
        try:stamp=epoch(row['time'])
        except (TypeError,ValueError):continue
        if row['status']==0:
            if current is None:
                message=(row['msg'] or '').lower()
                reason='检测请求失败'
                if 'timeout' in message or 'timed out' in message:reason='检测请求超时'
                elif 'query' in message or 'json' in message:reason='服务未报告就绪状态'
                elif any(s in message for s in ('dns','enotfound','eai_again')):reason='域名解析失败'
                elif 'refused' in message:reason='连接被拒绝'
                current={'start':stamp,'end':None,'reason':reason}
                incidents.append(current)
        elif row['status']==1 and current is not None:
            current['end']=stamp;current=None
    return incidents[-50:][::-1]


def epoch(value):
    if not value:
        return None
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()


def collect(root=BASE.parent / 'uptime-kuma', now=None):
    now = time.time() if now is None else now
    path = Path(root) / 'data' / 'kuma.db'
    if not path.is_file():
        return {'monitors': [], 'error': 'Uptime Kuma 尚未初始化'}
    try:
        with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            # One read transaction keeps current status, counts and curves consistent.
            db.execute('BEGIN')
            rows = db.execute('SELECT id,name FROM monitor WHERE active=1 ORDER BY id LIMIT 17').fetchall()
            monitors = []
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for row in rows[:16]:
                monitor = dict(row)
                recent = []
                for h in db.execute('SELECT status,time,ping FROM heartbeat WHERE monitor_id=? ORDER BY id DESC LIMIT 100', (row['id'],)):
                    try:
                        stamp = epoch(h['time'])
                    except (ValueError, TypeError):
                        continue
                    if stamp is not None:
                        recent.append({'time': stamp, 'status': h['status'], 'ping': h['ping'] if h['status'] == 1 else None})
                recent.reverse()
                latest = recent[-1] if recent else {}
                stale = latest.get('time') is None or now - latest['time'] > 120
                monitor.update(time=latest.get('time'), status=None if stale else latest.get('status'),
                               ping=None if stale else latest.get('ping'), recent=recent, stats={}, series={})
                monitor['incidents']=incident_history(db,row['id'],now)
                for key, (table, seconds, bucket) in WINDOWS.items():
                    if table not in tables:
                        monitor['stats'][key] = {'uptime': None, 'avg_ping': None, 'samples': 0}
                        monitor['series'][key] = []
                        continue
                    args = (row['id'], int(now - seconds), int(now))
                    counts = db.execute(f'SELECT SUM(up) AS u,SUM(down) AS d,SUM(CASE WHEN ping IS NOT NULL THEN ping*up ELSE 0 END) AS p,SUM(CASE WHEN ping IS NOT NULL THEN up ELSE 0 END) AS n FROM {table} WHERE monitor_id=? AND timestamp>=? AND timestamp<=?', args).fetchone()
                    up, down = counts['u'] or 0, counts['d'] or 0
                    monitor['stats'][key] = {'uptime': 100 * up / (up + down) if up + down else None,
                                             'avg_ping': counts['p'] / counts['n'] if counts['n'] else None,
                                             'samples': up + down}
                    points = db.execute(f'SELECT CAST(timestamp / {bucket} AS INTEGER)*{bucket} AS time,SUM(CASE WHEN ping IS NOT NULL THEN ping*up ELSE 0 END) AS p,SUM(CASE WHEN ping IS NOT NULL THEN up ELSE 0 END) AS n FROM {table} WHERE monitor_id=? AND timestamp>=? AND timestamp<=? GROUP BY 1 ORDER BY 1', args).fetchall()
                    monitor['series'][key] = [{'time': p['time'], 'ping': p['p'] / p['n'] if p['n'] else None} for p in points]
                monitors.append(monitor)
            result = {'monitors': monitors}
            if len(rows) > 16:
                result['error'] = '监控服务超过16项，当前展示前16项'
            return result
    except (OSError, sqlite3.Error) as exc:
        return {'monitors': [], 'error': '监控数据库暂不可读取：' + type(exc).__name__}


def main():
    mutex = None
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = kernel.CreateMutexW(None, False, 'Local\\ChatGPTSitesKumaSync')
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(mutex)
            return
    logs = BASE / 'logs'
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / 'kuma-sync.log', maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(message)s')
    try:
        config = json.loads((BASE / 'cloud_agent_config.json').read_text(encoding='utf-8-sig'))
        cloud = HTTP(config['endpoint'], config['worker_token'])
        while True:
            try:
                payload = collect()
                cloud.call('/worker/kuma', payload)
                (logs / 'kuma-sync-status.json').write_text(json.dumps({'updated': time.time(), 'monitors': len(payload['monitors']), 'ok': True}), encoding='utf-8')
                logging.info('监控同步成功：%s 项', len(payload['monitors']))
            except Exception as exc:
                # Do not log endpoint, credentials or response body.
                logging.warning('监控同步失败：%s，30秒后重试', type(exc).__name__)
            time.sleep(30)
    finally:
        if mutex:
            kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
