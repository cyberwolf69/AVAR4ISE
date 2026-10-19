"""Offline candidate audit, never connected to execution or alerts."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from hedging_tools import structure
from hedging_tools.scanner import screen, closed_candles
from hedging_tools.paths import CONFIG_PATH, RUNTIME_DIR


def break_bias(rows):
    """Hold latest confirmed close-break direction until its protected swing breaks.

    Uses only pivots available before each bar; an opposite close break changes bias.
    This is a research hypothesis, not an institutional-order inference.
    """
    points = structure.pivots(rows)
    consumed = set()
    direction, protected = 0, None
    for i in range(1, len(rows)):
        close = rows[i][3]
        if protected is not None and direction*(close-protected) <= 0:
            direction, protected = 0, None
        available = [p for p in points if p['confirmed'] < i]
        highs = [p for p in available if p['kind'] == 'HIGH']
        lows = [p for p in available if p['kind'] == 'LOW']
        if not highs or not lows:
            continue
        for sign, pivot, guard in ((1, highs[-1], lows[-1]), (-1, lows[-1], highs[-1])):
            key = (pivot['kind'],pivot['index'])
            level = pivot['price']
            if key not in consumed and sign*(rows[i-1][3]-level) <= 0 < sign*(close-level):
                consumed.add(key)
                direction, protected = sign, guard['price']
    return direction


def audit(database, limit=5000):
    if limit <= 0:
        raise ValueError('limit must be positive')
    config=json.loads(CONFIG_PATH.read_text())
    config['setup_model']='structure_bos_retest_v1'
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute('SELECT id,recorded_at,raw_json,result_json FROM scans ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    models={name:{'reasons':Counter(),'eligible_observations':0,'events':{}}
            for name in ('current_hh_hl','research_confirmed_break_bias')}
    unique=set(); duplicates=0; skipped=Counter(); times=[]; transitions=Counter()
    original_trend=structure.trend
    for ident, recorded, raw_text, result_text in reversed(rows):
        previous=json.loads(result_text)
        if previous.get('metrics',{}).get('structure',{}).get('model')!='structure_bos_retest_v1':
            skipped['not_structure_snapshot']+=1
            continue
        raw=json.loads(raw_text)
        if not raw or not previous.get('observed_at'):
            skipped['missing_snapshot_or_time']+=1
            continue
        now=datetime.fromisoformat(previous['observed_at'])
        # Deduplicate identical raw snapshots; events are deduplicated separately below.
        key=json.dumps(raw,sort_keys=True)
        if key in unique:
            duplicates+=1
            continue
        unique.add(key);times.append(now.isoformat())
        cfg={**config,'pairs':[raw['pair']]}
        reasons=[]
        for name,detector in (('current_hh_hl',original_trend),('research_confirmed_break_bias',break_bias)):
            # Local single-threaded offline replay only. No running engine imports this module.
            with patch.object(structure,'trend',detector):
                result=screen(raw,cfg,now)
            reason='ELIGIBLE' if result['status']=='WATCHLIST' else result['reasons'][0].split(':')[0]
            reasons.append(reason);models[name]['reasons'][reason]+=1
            if result['status']!='WATCHLIST':
                continue
            models[name]['eligible_observations']+=1
            candidate=result['candidate'];e=result['metrics']['structure']
            event=f"{raw['pair']}:{candidate['action']}:{e['bos_time']}"
            models[name]['events'].setdefault(event,{'scan_id':ident,'observed_at':now.isoformat(),
                'pair':raw['pair'],'candidate':candidate,'structure':e})
        transitions[' -> '.join(reasons)]+=1
    for model in models.values():
        model['unique_bos_events']=len(model['events'])
        model['events']=list(model['events'].values())
    return {'mode':'OFFLINE_STRUCTURE_AUDIT','activation':'NOT_APPLIED',
            'selected_rows':len(rows),'evaluated_observations':len(unique),
            'identical_snapshots_skipped':duplicates,'excluded':dict(skipped),
            'time_range':[min(times),max(times)] if times else [],'models':models,
            'transitions':dict(transitions),
            'limitations':['Candidate availability only; no profit, win rate or fill simulation.',
                'Saved selected-pair snapshots only; repeated candles are not independent samples.',
                'Same 15m retest, sizing, spread and freshness gates for both models.',
                'No AI calls, alerts, orders or active strategy changes.',
                'Research alternatives need out-of-sample evaluation before activation.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=RUNTIME_DIR/'scans.sqlite3')
    parser.add_argument('--limit',type=int,default=5000)
    parser.add_argument('--output',type=Path,default=RUNTIME_DIR/'structure-audit.json')
    args=parser.parse_args()
    report=audit(args.db,args.limit)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
