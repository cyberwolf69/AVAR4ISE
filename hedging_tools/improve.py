"""Offline post-trade reviews and exploratory filter replay. Never applies changes."""
import argparse
import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.paths import RUNTIME_DIR
from hedging_tools.risk_engine import number as D

RULES = (
    {'name': 'MIN_RVOL_1_2', 'feature': 'rvol', 'operator': '>=', 'threshold': '1.2'},
    {'name': 'MAX_SPREAD_0_05_PERCENT', 'feature': 'spread', 'operator': '<=', 'threshold': '0.0005'},
    {'name': 'MAX_SNAPSHOT_AGE_60_SECONDS', 'feature': 'age_seconds', 'operator': '<=', 'threshold': '60'},
)
LOCKED = {'max_positions': 5, 'margin_usdt': '5', 'leverage': [10, 30], 'daily_loss_fraction': '0.10', 'entry_risk_usdt': '1'}
LIMITATIONS = ('Retrospective entry-filter replay, not a new fill simulation. No stop/target changes are simulated. '
               'Excludes capital reuse and trade interactions. Holdout is exploratory, not proof of future performance. '
               'Reference outcomes never become actual trade PnL. No automatic activation.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return int(parsed.timestamp()*1000)


def load_rows(path, query):
    if not path.exists():
        return []
    # Source databases are opened read-only; review cannot change trading state.
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
        return [json.loads(row[0]) for row in db.execute(query)]


def features(evidence):
    metrics = evidence.get('metrics', {})
    out = {}
    try:
        out['rvol'] = str(D(metrics['15m']['rvol']))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        pass
    try:
        quote = evidence.get('entry_quote')
        if quote:
            bid, ask = D(quote['bidPx']), D(quote['askPx'])
            if not 0 < bid <= ask:
                raise ValueError('Invalid spread')
            out['spread'] = str((ask-bid)/bid)
        else:
            spread = D(metrics['spread_fraction'])
            if spread < 0:
                raise ValueError('Invalid spread')
            out['spread'] = str(spread)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        pass
    try:
        age = (timestamp(evidence['captured_at']) - timestamp(evidence['market_observed_at'])) / 1000
        if age < 0:
            raise ValueError('Future snapshot')
        out['age_seconds'] = str(age)
    except (KeyError, TypeError, ValueError):
        pass
    return out


def collect(portfolio, signals):
    records, reviews, excluded = [], [], Counter()
    for source, items in (('ACTUAL_DEMO', portfolio.get('positions', [])), ('REFERENCE_SIGNAL', signals)):
        for item in items:
            status = item.get('status')
            if source == 'ACTUAL_DEMO' and status != 'CLOSED':
                continue
            if source == 'REFERENCE_SIGNAL' and (status not in ('SL', 'PROTECTED_STOP', 'TP3') or item.get('quality') != 'OK'):
                excluded['reference_open_ambiguous_expired_timeout_or_incomplete'] += 1
                continue
            try:
                evidence = item.get('entry_evidence') or {}
                if not isinstance(evidence, dict):
                    evidence = {}
                f = features(evidence)
                outcome = item.get('exit_kind', 'UNKNOWN') if source == 'ACTUAL_DEMO' else status
                pnl = D(item.get('pnl', '0')) if source == 'ACTUAL_DEMO' else None
                orders = item.get('execution_evidence', [])
                # Old negative PnL without linked stop evidence is not proof of an SL.
                if source == 'ACTUAL_DEMO' and outcome in ('SL', 'PROTECTED_STOP'):
                    if not any(o.get('role') == 'SL' and D(o.get('accFillSz') or 0) > 0 for o in orders):
                        outcome = 'UNKNOWN'
                key = source + ':' + item['id']
                findings = []
                if outcome == 'PROTECTIVE_EXIT':
                    findings.append({'kind': 'EXECUTION_PROTECTION', 'evidence': item.get('protective_exit_reason', 'Unknown protection issue')})
                if 'spread' in f and D(f['spread']) > D('.0005'):
                    findings.append({'kind': 'WIDE_ENTRY_SPREAD', 'value': f['spread'], 'causal': False})
                if 'rvol' in f and D(f['rvol']) < D('1.2'):
                    findings.append({'kind': 'LOW_RELATIVE_VOLUME', 'value': f['rvol'], 'causal': False})
                if 'age_seconds' in f and D(f['age_seconds']) > 60:
                    findings.append({'kind': 'OLDER_SNAPSHOT', 'value': f['age_seconds'], 'causal': False})
                if pnl is not None and item.get('risk_budget') and -pnl > D(item['risk_budget']):
                    findings.append({'kind': 'LOSS_EXCEEDS_RESERVED_RISK_EXCLUDING_FUNDING', 'value': str(-pnl)})
                if outcome in ('SL', 'PROTECTED_STOP', 'PROTECTIVE_EXIT') or (pnl is not None and pnl < 0):
                    reviews.append({'id': key, 'source': source, 'pair': item.get('pair'), 'outcome': outcome,
                                    'conclusion': 'CAUSE_NOT_ESTABLISHED' if outcome != 'PROTECTIVE_EXIT' else 'PROTECTION_ISSUE_RECORDED',
                                    'findings': findings, 'missing_features': sorted(set(('spread', 'rvol', 'age_seconds'))-set(f)),
                                    'pnl_excluding_funding': str(pnl) if pnl is not None else None,
                                    'recorded_funding': item.get('funding_bills', {}),
                                    'entry_evidence': evidence, 'execution_evidence': orders,
                                    'planned_trade': {k: item.get(k) for k in ('direction', 'size', 'entry', 'limit_price', 'stop', 'leverage', 'margin_upper_bound', 'planned_risk', 'risk_budget')},
                                    'exit_stop_trigger': item.get('exit_stop_trigger'),
                                    'stop_history': item.get('stop_history', []), 'reference_events': item.get('events', [])})
                version = evidence.get('strategy_version')
                if not version or len(f) != 3:
                    excluded[source+':missing_entry_evidence'] += 1
                    continue
                if source == 'ACTUAL_DEMO':
                    if outcome not in ('SL', 'PROTECTED_STOP', 'TP3') or not orders:
                        excluded['actual_unverified_or_non_strategy_exit'] += 1
                        continue
                    # Replay metric requires the terminal order-level cashflows to match.
                    if any(o.get('state') not in ('filled', 'canceled') for o in orders):
                        raise ValueError('Unsettled fills')
                    cashflows = sum((D(o['pnl']) + D(o['fee']) for o in orders if D(o.get('accFillSz') or 0)), D(0))
                    if cashflows != pnl or any(o.get('feeCcy') != 'USDT' for o in orders if D(o.get('accFillSz') or 0)):
                        raise ValueError('Unverified cashflows')
                    start, end = int(item['opened_at']), int(item['closed_at'])
                else:
                    start = int(item['issued_ms'])
                    end = max(int(e['bar_end_ms']) for e in item['events'])
                    version += ':' + item.get('model', 'UNKNOWN')
                if start > end:
                    raise ValueError('Invalid event timeline')
                records.append({'id': key, 'source': source, 'version': version, 'start': start, 'end': end,
                                'features': f, 'outcome': outcome, 'pnl': str(pnl) if pnl is not None else None})
            except (KeyError, ValueError, TypeError, ArithmeticError):
                excluded[source+':invalid_evidence'] += 1
    return records, reviews, dict(excluded)


def retained(record, rule):
    value, threshold = D(record['features'][rule['feature']]), D(rule['threshold'])
    return value >= threshold if rule['operator'] == '>=' else value <= threshold


def replay(records, rule):
    kept = [r for r in records if retained(r, rule)]
    rejected = [r for r in records if not retained(r, rule)]
    actual = records[0]['source'] == 'ACTUAL_DEMO'
    winners = [r for r in records if D(r['pnl']) > 0] if actual else [r for r in records if r['outcome'] == 'TP3']
    kept_ids = {r['id'] for r in kept}
    retention = D(len(kept))/len(records)
    winner_retention = D(sum(r['id'] in kept_ids for r in winners))/len(winners) if winners else D(0)
    baseline = sum((D(r['pnl']) for r in records), D(0)) if actual else D(sum(r['outcome'] == 'SL' for r in records))
    candidate = sum((D(r['pnl']) for r in kept), D(0)) if actual else D(sum(r['outcome'] == 'SL' for r in kept))
    improvement = candidate-baseline if actual else baseline-candidate
    return {'n': len(records), 'retained': len(kept), 'rejected': len(rejected), 'retention': str(retention),
            'winner_retention': str(winner_retention), 'baseline': str(baseline), 'candidate': str(candidate),
            'improvement': str(improvement),
            'metric': 'PNL_USDT_EXCLUDING_FUNDING' if actual else 'INITIAL_SL_COUNT_NOT_PNL',
            'passes': retention >= D('.70') and winner_retention >= D('.80') and improvement > 0 and len(rejected) >= 2}


def evaluate_group(records):
    records = sorted(records, key=lambda r: (r['start'], r['id']))
    # Last third is never used to choose a threshold. Purge overlapping training labels.
    split = len(records)*2//3
    holdout = records[split:]
    boundary = holdout[0]['start'] if holdout else 0
    training = [r for r in records[:split] if r['end'] < boundary]
    base = {'source': records[0]['source'], 'strategy_version': records[0]['version'],
            'dataset_hash': digest(records), 'train_ids': [r['id'] for r in training],
            'holdout_ids': [r['id'] for r in holdout], 'purged_overlap': split-len(training),
            'method': 'CHRONOLOGICAL_PURGED_FILTER_REPLAY', 'locked_risk': LOCKED,
            'limitations': LIMITATIONS, 'activation': 'NOT_APPLIED'}
    if len(training) < 20 or len(holdout) < 10 or sum(r['outcome'] == 'SL' for r in training) < 5:
        return dict(base, status='INSUFFICIENT_DATA', requirement='At least 20 train / 10 holdout, and 5 initial SL in train, same source/version')
    candidates = [(rule, replay(training, rule)) for rule in RULES]
    eligible = [(rule, result) for rule, result in candidates if result['passes']]
    if not eligible:
        return dict(base, status='NO_SUPPORTED_CHANGE', training_results=[{'rule': r, **v} for r, v in candidates])
    # Winner is selected on training data only. A failed holdout does not try another rule.
    rule, training_result = max(eligible, key=lambda pair: D(pair[1]['improvement']))
    validation = replay(holdout, rule)
    return dict(base, status='NEEDS_USER_REVIEW' if validation['passes'] else 'HOLDOUT_FAILED',
                rule=rule, training=training_result, holdout=validation,
                interpretation='Association only; fewer selected historical trades/signals, not an established cause of SL')


def build_report(portfolio, signals, evaluate=False):
    records, reviews, excluded = collect(portfolio, signals)
    groups = {}
    for record in records:
        groups.setdefault((record['source'], record['version']), []).append(record)
    evaluations = [evaluate_group(group) for group in groups.values()] if evaluate else []
    for result in evaluations:
        result['proposal_id'] = digest(result)[:16]
    patterns = Counter((r['source'], f['kind']) for r in reviews for f in r['findings'])
    return {'mode': 'IMPROVE_REVIEW_ONLY', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'status': 'NO_CLOSED_EVIDENCE' if not reviews and not records else 'REVIEW_READY',
            'locked_risk': LOCKED, 'activation': 'NOT_APPLIED', 'reviews': reviews,
            'patterns': [{'source': source, 'finding': kind, 'count': count} for (source, kind), count in patterns.items()],
            'eligible_samples': {source: sum(r['source'] == source for r in records) for source in ('ACTUAL_DEMO', 'REFERENCE_SIGNAL')},
            'excluded': excluded, 'evaluations': evaluations, 'limitations': LIMITATIONS}


def persist(report, path):
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS reviews (id TEXT, revision TEXT, data TEXT, PRIMARY KEY(id,revision))')
        db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, status TEXT, data TEXT)')
        for review in report['reviews']:
            db.execute('INSERT OR IGNORE INTO reviews VALUES (?,?,?)', (review['id'], digest(review), json.dumps(review)))
        for proposal in report['evaluations']:
            db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?,?)', (proposal['proposal_id'], proposal['status'], json.dumps(proposal)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluate', action='store_true', help='Replay fixed entry filters on chronological train/holdout data')
    parser.add_argument('--details', action='store_true', help='Print individual case reviews')
    args = parser.parse_args()
    errors = []
    try:
        stored = load_rows(RUNTIME_DIR/'portfolio.sqlite3', 'SELECT data FROM portfolio WHERE id=1')
        portfolio = stored[0] if stored else {}
    except (sqlite3.Error, ValueError, OSError) as exc:
        portfolio = {}
        errors.append({'source': 'ACTUAL_DEMO', 'error': type(exc).__name__})
    try:
        signals = load_rows(RUNTIME_DIR/'signal-history.sqlite3', 'SELECT data FROM tracked_signals')
    except (sqlite3.Error, ValueError, OSError) as exc:
        signals = []
        errors.append({'source': 'REFERENCE_SIGNAL', 'error': type(exc).__name__})
    report = build_report(portfolio, signals, args.evaluate)
    report['source_errors'] = errors
    if errors:
        report['status'] = 'INCOMPLETE_SOURCE_DATA'
    persist(report, RUNTIME_DIR/'improve.sqlite3')
    path = RUNTIME_DIR/'improve-output.json'
    temporary = path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    temporary.replace(path)
    shown = report if args.details else {k: v for k, v in report.items() if k != 'reviews'}
    print(json.dumps(dict(shown, review_count=len(report['reviews']), output=str(path)), indent=2, ensure_ascii=False))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
