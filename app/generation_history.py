"""Persistent local generation records; never replay interrupted desktop tasks."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3


class GenerationHistory:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.database = self.folder / 'records.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, request_id TEXT, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS records_created ON records(created DESC, id DESC)')
            db.execute('CREATE INDEX IF NOT EXISTS records_request ON records(request_id)')
            rows = db.execute("SELECT id,payload FROM records WHERE status IN ('queued','running','review')").fetchall()
            for job_id, raw in rows:
                data = json.loads(raw)
                data.update(status='error', stage='服务器重启，原任务已中断，未自动重新生成。', error='服务器重启中断任务，请检查后手动提交新任务。')
                data.pop('queue_position', None)
                db.execute('UPDATE records SET status=?,payload=? WHERE id=?', ('error', json.dumps(data, ensure_ascii=False), job_id))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, data):
        with self.connect() as db:
            db.execute('INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload',
                       (data['id'], data['created_at'], data['status'], data.get('request_id'), json.dumps(data, ensure_ascii=False)))

    def get(self, job_id=None, request_id=None):
        with self.connect() as db:
            if job_id is not None:
                row = db.execute('SELECT payload FROM records WHERE id=?', (job_id,)).fetchone()
            else:
                row = db.execute('SELECT payload FROM records WHERE request_id=? ORDER BY created DESC LIMIT 1', (request_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def public(data):
        return {key:value for key,value in data.items() if not key.startswith('_') and key != 'request_id'}

    def page(self, offset=0, limit=20, status=None):
        where = ' WHERE status=?' if status else ''
        params = (status,) if status else ()
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM records'+where, params).fetchone()[0]
            rows = db.execute('SELECT payload FROM records'+where+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?', params+(limit,offset)).fetchall()
        return {'items':[self.public(json.loads(row[0])) for row in rows], 'total':total, 'offset':offset, 'limit':limit}

    def preserve_image(self, job_id, source):
        folder = self.folder / 'images'
        folder.mkdir(exist_ok=True)
        target = folder / (job_id + source.suffix.lower())
        shutil.copyfile(source, target)
        return target
