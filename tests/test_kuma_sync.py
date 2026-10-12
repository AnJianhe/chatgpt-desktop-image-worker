import sys
import sqlite3
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from kuma_sync import collect


class KumaSyncTests(unittest.TestCase):
    def test_real_counts_weighted_response_stale_and_no_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            with sqlite3.connect(root / 'data/kuma.db') as db:
                db.executescript('CREATE TABLE monitor(id INTEGER,name TEXT,active INTEGER);CREATE TABLE heartbeat(id INTEGER,monitor_id INTEGER,status INTEGER,time TEXT,ping REAL,msg TEXT);')
                db.executemany('INSERT INTO monitor VALUES(?,?,1)', [(1, '画图服务器'), (2, '云端入口')])
                db.executemany('INSERT INTO heartbeat VALUES(?,?,?,?,?,NULL)', [(1, 1, 1, '2026-10-11 00:00:00', 12), (2, 1, 0, '2026-10-11 00:00:10', 0), (3, 1, 1, '2026-10-11 00:00:20', 30)])
                now = 1791676820  # 2026-10-11 00:00:20 UTC
                for table in ('stat_minutely', 'stat_hourly', 'stat_daily'):
                    db.execute(f'CREATE TABLE {table}(monitor_id INTEGER,timestamp INTEGER,up INTEGER,down INTEGER,ping REAL)')
                    db.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?)', [(1, now-60, 3, 1, 10), (1, now, 1, 1, 30), (1, now-400*86400, 99, 0, 999)])
            db.close()
            result = collect(root, now)
            m = result['monitors'][0]
            self.assertEqual(m['status'], 1)
            self.assertEqual(len(m['incidents']),1)
            self.assertIsNotNone(m['incidents'][0]['end'])
            self.assertEqual(m['ping'], 30)
            self.assertEqual(m['recent'][1]['ping'], None)
            for stats in m['stats'].values():
                self.assertAlmostEqual(stats['uptime'], 100*4/6)
                self.assertEqual(stats['avg_ping'], 15)
                self.assertEqual(stats['samples'], 6)
            self.assertIsNone(result['monitors'][1]['stats']['24h']['uptime'])
            self.assertIsNone(collect(root, now+121)['monitors'][0]['status'])

    def test_missing_database_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = collect(Path(directory))
            self.assertEqual(result['monitors'], [])
            self.assertIn('初始化', result['error'])


if __name__ == '__main__':
    unittest.main()
