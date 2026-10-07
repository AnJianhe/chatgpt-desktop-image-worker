import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
spec = importlib.util.spec_from_file_location('link_sync',Path(__file__).resolve().parents[1]/'app/link_sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

class SyncTest(unittest.TestCase):
    def test_old_watchdog_api_address_cannot_be_uploaded_as_a_tunnel(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'status.json'
            for state in ['可访问', '原地址重连中']:
                p.write_text(json.dumps({'url':'https://api.trycloudflare.com', 'state':state}), encoding='utf-8')
                status = sync.read_status(p)
                self.assertEqual(status['url'], '')
                self.assertEqual(status['state'], '状态已过期')

    def test_status_freshness_and_invalid_url(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'status.json'
            self.assertEqual(sync.read_status(p)['state'],'状态已过期')
            p.write_text(json.dumps({'url':'https://cat.trycloudflare.com/','state':'可访问'}),encoding='utf-8')
            modified=p.stat().st_mtime
            self.assertEqual(sync.read_status(p,modified+5)['url'],'https://cat.trycloudflare.com')
            self.assertEqual(sync.read_status(p,modified+50)['state'],'状态已过期')
            p.write_text(json.dumps({'url':'https://evil.test','state':'可访问'}))
            self.assertEqual(sync.read_status(p)['url'],'')
            p.write_text('broken')
            self.assertEqual(sync.read_status(p)['state'],'状态已过期')
    def test_invalid_config(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json'
            p.write_text(json.dumps({'endpoint':'http://example.test/api/update','token':'a'*64}))
            with self.assertRaises(ValueError):sync.config(p)

if __name__=='__main__':unittest.main()
