"""Forward-only structural comparison. Public prices only; no AI, messages or orders."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from unittest.mock import patch
from hedging_tools import structure
from hedging_tools.controls import Controls
from hedging_tools.structure_audit import break_bias
from hedging_tools.scanner import screen
from hedging_tools.analyst import make_packet
from hedging_tools.signal_history import SignalHistory
from hedging_tools.okx_public import OKXPublic
from hedging_tools.improve_evidence import strategy_version
from hedging_tools.paths import RUNTIME_DIR, PACKAGE_DIR, CONFIG_PATH

MODELS = ('baseline', 'break_bias')


def version():
    payload = strategy_version().encode() + b''.join((PACKAGE_DIR/p).read_bytes() for p in
                    ('shadow.py','structure_audit.py','signal_history.py'))
    return hashlib.sha256(payload).hexdigest()[:16]


class Shadow:
    def __init__(self, root, source, experiment):
        self.root = Path(root)/experiment
        self.root.mkdir(parents=True,exist_ok=True)
        self.source = Path(source)
        self.path = self.root/'observations.sqlite3'
        self.histories = {m:SignalHistory(self.root/(m+'.sqlite3')) for m in MODELS}
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS observations (scan_id INTEGER,model TEXT,data TEXT,PRIMARY KEY(scan_id,model))')
            db.execute('CREATE TABLE IF NOT EXISTS events (model TEXT,event TEXT,PRIMARY KEY(model,event))')

    def collect(self, now=None):
        now = now or datetime.now(timezone.utc)
        if not self.source.exists():
            return {'status':'WAITING_FOR_SCANS'}
        with closing(sqlite3.connect(self.source.resolve().as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as db,db:
                row=db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
                if row is None:
                    latest=source.execute('SELECT COALESCE(MAX(id),0) FROM scans').fetchone()[0]
                    db.executemany('INSERT INTO meta VALUES (?,?)',[('cursor',str(latest)),('started_at',now.isoformat())])
                    return {'status':'FORWARD_TRACKING_STARTED','after_scan_id':latest}
                cursor=int(row[0])
                records=source.execute('SELECT id,raw_json,result_json FROM scans WHERE id>? ORDER BY id LIMIT 200',(cursor,)).fetchall()
                config=json.loads(CONFIG_PATH.read_text())
                controls=Controls().read()
                config.update(min_leverage=controls['min_leverage'],max_leverage=controls['max_leverage'],margin_per_trade=controls['margin'])
                detector=structure.trend
                for ident,raw_json,result_json in records:
                    raw=json.loads(raw_json);previous=json.loads(result_json)
                    observed=datetime.fromisoformat(previous['observed_at']) if previous.get('observed_at') else None
                    fresh=observed is not None and observed.tzinfo is not None and 0 <= (now-observed).total_seconds() <= 180
                    for model in MODELS:
                        result={'status':'SKIPPED','reasons':['STALE_OR_MISSING_SNAPSHOT']}
                        if raw and fresh:
                            cfg={**config,'pairs':[raw['pair']],'setup_model':'structure_bos_retest_v1'}
                            with patch.object(structure,'trend',detector if model=='baseline' else break_bias):
                                result=screen(raw,cfg,now)
                            if result.get('metrics',{}).get('structure'):
                                result['metrics']['structure']['comparison_model']=model
                            if result['status']=='WATCHLIST':
                                c=result['candidate'];e=result['metrics']['structure']
                                event=f"{raw['pair']}:{c['action']}:{e['bos_time']}"
                                exists=db.execute('SELECT 1 FROM events WHERE model=? AND event=?',(model,event)).fetchone()
                                if not exists:
                                    envelope=make_packet(result,[],[],now)
                                    data=envelope['input']
                                    proposal={'packet_id':data['packet_id'],'pair':data['pair'],'action':c['action'],
                                        'reason':'Deterministic shadow candidate; not an AI recommendation.',
                                        'invalidation':'Structure stop '+c['stop_reference'],
                                        'source_ids':[],'expires_at':data['expires_at']}
                                    self.histories[model].register(envelope,proposal,now,
                                        reference_id='shadow:'+hashlib.sha256(event.encode()).hexdigest())
                                    db.execute('INSERT INTO events VALUES (?,?)',(model,event))
                        db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?)',(ident,model,json.dumps(result)))
                    db.execute("UPDATE meta SET value=? WHERE key='cursor'",(str(ident),))
        return {'status':'CHECKED','scans_consumed':len(records)}

    def outcomes_due(self, now=None):
        now = now or datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT value FROM meta WHERE key='last_outcome_poll'").fetchone()
            if row and now.timestamp() - float(row[0]) < 60:
                return False
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_outcome_poll',?)", (str(now.timestamp()),))
        return True

    def report(self):
        with closing(sqlite3.connect(self.path)) as db:
            meta=dict(db.execute('SELECT key,value FROM meta'))
            observations={m:[] for m in MODELS}
            for model,data in db.execute('SELECT model,data FROM observations'):
                observations[model].append(json.loads(data))
        models={}
        for m in MODELS:
            reasons=Counter('ELIGIBLE' if r['status']=='WATCHLIST' else r['reasons'][0].split(':')[0] for r in observations[m])
            models[m]={'observations':len(observations[m]),'reasons':dict(reasons),**self.histories[m].summary()}
        return {'mode':'SHADOW_NO_EXECUTION','experiment':self.root.name,'started_at':meta.get('started_at'),
                'models':models,'activation':'NOT_APPLIED',
                'limitations':'Forward reference touches only, no AI approval or account capacity gate. No fill, fee, funding, net PnL or win-rate claims. No automatic promotion.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update',action='store_true',help='Consume new scans and update outcomes with public mark candles')
    args=parser.parse_args()
    root=RUNTIME_DIR/'shadow';root.mkdir(exist_ok=True)
    with (root/'.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'SHADOW_WORKER_BUSY'}));return 0
        shadow=Shadow(root,RUNTIME_DIR/'scans.sqlite3',version())
        updates={}
        if args.update:
            updates['collection']=shadow.collect()
            if shadow.outcomes_due():
                client=OKXPublic()
                for model,history in shadow.histories.items():
                    updates[model]=history.update(client,max_pairs=5)
        report={**shadow.report(),'updates':updates}
        target=RUNTIME_DIR/'shadow-output.json'
        tmp=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        tmp.write_text(json.dumps(report,indent=2)+'\n');tmp.replace(target)
        print(json.dumps(report,indent=2))
    return 0


if __name__=='__main__':
    main()
