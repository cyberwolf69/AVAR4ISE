from contextlib import closing
import fcntl
import http.client
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.dashboard import snapshot, Dashboard


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=1800000000
        self.stamp=datetime.fromtimestamp(self.now,timezone.utc).isoformat()
    def write(self,name,value): (self.root/name).write_text(json.dumps(value))
    def lock(self):
        file=(self.root/'.demo-agent.lock').open('a');self.addCleanup(file.close)
        fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def test_saved_running_heartbeat_without_process_is_offline(self):
        self.write('engine-heartbeat.json',dict(status='RUNNING',time=self.stamp))
        s=snapshot(self.root,self.now)
        self.assertEqual(s['engine']['status'],'OFFLINE')
        self.assertTrue(all(m['state']=='OFFLINE' for m in s['modules']))
    def test_live_scanner_and_stale_heartbeat(self):
        self.lock();self.write('engine-heartbeat.json',dict(status='RUNNING',time=self.stamp))
        self.write('portfolio-output.json',dict(status='SCANNING',time=self.stamp))
        self.write('ai-activity.json',dict(status='REVIEWING',time=self.stamp,pair='BTC-USDT-SWAP'))
        s=snapshot(self.root,self.now+3)
        self.assertEqual(s['engine']['status'],'RUNNING')
        self.assertEqual(s['modules'][0]['state'],'WORKING')
        self.assertEqual(s['modules'][1]['state'],'WORKING')
        self.assertEqual(snapshot(self.root,self.now+31)['engine']['status'],'STALE')
    def test_old_provider_failure_is_not_active_cooldown(self):
        self.write('research-output.json',dict(provider_health=dict(reason='PAYMENT_OR_ACCESS_REQUIRED',retry_at=self.now-1,failures=1)))
        s=snapshot(self.root,self.now)
        self.assertEqual(s['provider']['state'],'RETRY_READY')
        self.assertEqual(s['provider']['remaining_seconds'],0)
    def test_allowlist_excludes_credentials_and_private_order_details(self):
        (self.root/'.env').write_text('API_KEY=SECRET_NEVER_EXPOSE')
        with closing(sqlite3.connect(self.root/'portfolio.sqlite3')) as db, db:
            db.execute('CREATE TABLE portfolio(id INTEGER,data TEXT)')
            db.execute('INSERT INTO portfolio VALUES(1,?)',(json.dumps(dict(equity='50',secret='SECRET_NEVER_EXPOSE',positions=[dict(id='1',status='OPEN',pair='BTC',credential='SECRET_NEVER_EXPOSE',execution_evidence=['SECRET_NEVER_EXPOSE'])])),))
        result=json.dumps(snapshot(self.root,self.now))
        self.assertNotIn('SECRET_NEVER_EXPOSE',result)
        self.assertNotIn('execution_evidence',result)
    def test_corrupt_data_is_reported(self):
        (self.root/'research-output.json').write_text('{broken')
        self.assertIn('research-output.json unavailable',snapshot(self.root,self.now)['warnings'])
    def test_reference_results_do_not_change_equity(self):
        with closing(sqlite3.connect(self.root/'signal-history.sqlite3')) as db, db:
            db.execute('CREATE TABLE tracked_signals(next_ms INTEGER,data TEXT)')
            db.execute('INSERT INTO tracked_signals VALUES(1,?)',(json.dumps(dict(id='ref',status='TP3',stage=3)),))
        s=snapshot(self.root,self.now)
        self.assertIsNone(s['portfolio']['equity'])
        self.assertEqual(s['signals'][0]['stage'],3)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.dashboard=Dashboard(0,Path(self.tmp.name))
        self.dashboard.start();self.addCleanup(self.dashboard.close)
        self.port=self.dashboard.server.server_port
    def request(self,path='/',headers=None,method='GET'):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        conn.request(method,path,headers=headers or {})
        r=conn.getresponse();body=r.read();status=r.status;conn.close();return status,body
    def test_read_only_and_static_allowlist(self):
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.request('/api/state')[0],200)
        for path in ('/.env','/runtime/portfolio.sqlite3','/../.env'):
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/api/state',method='POST')[0],501)
    def test_foreign_origin_host_and_fetch_are_rejected(self):
        for headers in ({'Host':'evil.example'},{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/state',headers)[0],403)
