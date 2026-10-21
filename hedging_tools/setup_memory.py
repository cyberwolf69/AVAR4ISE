"""Versioned setup chronology, not a learned win-rate or an execution permission."""
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path


def identity(market, version):
    structure = market.get('metrics',{}).get('structure',{})
    if not structure.get('bos_time') or not structure.get('htf_direction'):
        return None
    key = [version,market['pair'],structure.get('model'),structure['htf_direction'],structure['bos_time']]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


class SetupMemory:
    def __init__(self, path, version):
        self.path, self.version = path, version
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS setup_memory (packet_id TEXT PRIMARY KEY, setup_id TEXT, at REAL, data TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS setup_memory_lookup ON setup_memory(setup_id,at)')

    def context(self, market, now):
        key = identity(market,self.version)
        context = {'scope':'SAME_SETUP_HISTORY_NOT_TRADE_PNL', 'setup_id':key,
                   'strategy_version':self.version, 'previous_reviews':[]}
        if not key:
            return context
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT data FROM setup_memory WHERE setup_id=? AND at<? ORDER BY at DESC LIMIT 3',
                            (key,now.timestamp())).fetchall()
        for row in reversed(rows):
            item=json.loads(row[0])
            # Outcomes are reference touches only, never actual execution or a win label.
            ref = self.reference(item.get('reference_id'),now.timestamp())
            if ref:
                item['reference_outcome']=ref
            context['previous_reviews'].append(item)
        return context

    def reference(self, reference_id, now):
        path=Path(self.path).parent/'signal-history.sqlite3'
        if not reference_id or not path.exists():
            return None
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute('SELECT data FROM tracked_signals WHERE id=?',(reference_id,)).fetchone()
            if not row:
                return None
            state=json.loads(row[0])
            # Do not supply outcomes from a later observation in a historical replay.
            if state.get('next_ms',float('inf')) > now*1000 or state.get('issued_ms',float('inf'))>now*1000:
                return None
            return {k:state.get(k) for k in ('status','stage','quality','model','actual_trade_pnl')}
        except (sqlite3.Error,ValueError):
            return None

    def record(self, envelope, analysis, now, reference_id=None):
        market=envelope['input']['market']
        key=identity(market,self.version)
        if not key:
            return
        p=analysis.get('proposal',{})
        data={'at':now.isoformat(), 'screen_status':market['status'],
              'screen_reasons':market.get('reasons',[])[:3],
              'structure':market.get('metrics',{}).get('structure',{}),
              'review_status':analysis['status'], 'action':p.get('action'),
              'candidate':{k:market.get('candidate',{}).get(k) for k in ('action','entry','stop_reference')},
              'supporting_evidence':p.get('supporting_evidence',[]),
              'opposing_evidence':p.get('opposing_evidence',[]),
              'reason':p.get('reason','')[:450], 'invalidation':p.get('invalidation','')[:220],
              'wait_for':p.get('wait_for','')[:220], 'reference_id':reference_id}
        # Keep actual reviews and changes in scanner status, not repeated polling noise.
        with closing(sqlite3.connect(self.path)) as db, db:
            previous=db.execute('SELECT data FROM setup_memory WHERE setup_id=? ORDER BY at DESC LIMIT 1',(key,)).fetchone()
            if previous and analysis['status']=='SKIPPED':
                old=json.loads(previous[0])
                if (old['screen_status'],old['screen_reasons'])==(data['screen_status'],data['screen_reasons']):
                    return
            db.execute('INSERT OR IGNORE INTO setup_memory VALUES (?,?,?,?)',
                       (envelope['input']['packet_id'],key,now.timestamp(),json.dumps(data)))
