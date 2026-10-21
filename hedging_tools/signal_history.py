"""Reference signal outcomes, independent of orders and strategy PnL."""
import argparse
import copy
import json
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.alerts import signal_message
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paths import RUNTIME_DIR
from hedging_tools.position_policy import COST_BUFFER, targets
from hedging_tools.risk_engine import number as D
from hedging_tools.improve_evidence import snapshot

MINUTE = 60000
DAY = 86400000
LIVE = ('PENDING', 'ACTIVE')


def millis(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def event(state, kind, timestamp):
    state['events'].append({'event': kind, 'bar_end_ms': timestamp})


def evaluate(original, rows, now_ms):
    """Closed mark candles only. Unknown intrabar ordering is never guessed."""
    state = copy.deepcopy(original)
    if state['status'] not in LIVE:
        return state
    candles = {}
    for row in rows:
        if len(row) != 6 or row[5] not in ('0', '1'):
            raise ValueError('Invalid mark-candle schema')
        timestamp = int(row[0])
        if row[5] == '0' or timestamp < state['next_ms'] or timestamp + MINUTE > now_ms:
            continue
        o, high, low, close = map(D, row[1:5])
        if timestamp % MINUTE or min(o, high, low, close) <= 0 or not low <= min(o, close) <= max(o, close) <= high:
            raise ValueError('Invalid closed mark candle')
        if timestamp in candles and candles[timestamp] != (o, high, low, close):
            raise ValueError('Conflicting mark candles')
        candles[timestamp] = (o, high, low, close)
    state['quality'] = 'OK'
    sign = 1 if state['direction'] == 'LONG' else -1
    entry = D(state['entry'])
    for timestamp in sorted(candles):
        if state['status'] not in LIVE:
            break
        if timestamp != state['next_ms']:
            state['quality'] = 'DATA_GAP'
            break
        end = timestamp + MINUTE
        _, high, low, _ = candles[timestamp]
        state['next_ms'] = end
        entry_bar = False
        if state['status'] == 'PENDING':
            if timestamp >= state['expires_ms']:
                state['status'] = 'EXPIRED'
                event(state, 'EXPIRED', timestamp)
                break
            touch = low <= entry <= high
            if touch and end > state['expires_ms']:
                state['status'] = 'AMBIGUOUS'
                event(state, 'ENTRY_OR_EXPIRY_ORDER_UNKNOWN', end)
                break
            if touch:
                state.update(status='ACTIVE', entered_ms=end, deadline_ms=end+DAY)
                event(state, 'ENTRY_REFERENCE_TOUCHED', end)
                entry_bar = True
            else:
                if end >= state['expires_ms']:
                    state['status'] = 'EXPIRED'
                    event(state, 'EXPIRED', end)
                continue
        stop_hit = low <= D(state['current_stop']) if sign > 0 else high >= D(state['current_stop'])
        reached = [i for i, target in enumerate(state['targets'], 1)
                   if i > state['stage'] and (high >= D(target) if sign > 0 else low <= D(target))]
        if (entry_bar and (stop_hit or reached)) or (stop_hit and reached):
            state['status'] = 'AMBIGUOUS'
            event(state, 'INTRABAR_ORDER_UNKNOWN', end)
            break
        if stop_hit:
            state['status'] = 'SL' if state['stage'] == 0 else 'PROTECTED_STOP'
            event(state, state['status'], end)
            break
        if reached:
            stage = max(reached)
            moved_stage = max((i for i in reached if i < 3), default=0)
            if moved_stage:
                new_stop = entry * (1 + sign*COST_BUFFER)
                if moved_stage == 2:
                    one_r = D(state['targets'][0])
                    new_stop = max(one_r, new_stop) if sign > 0 else min(one_r, new_stop)
                # The low/high may precede or follow the TP within the same minute.
                if (low <= new_stop if sign > 0 else high >= new_stop):
                    state['status'] = 'AMBIGUOUS'
                    event(state, 'TP_OR_MOVED_STOP_ORDER_UNKNOWN', end)
                    break
                state['current_stop'] = str(new_stop)
            for i in reached:
                event(state, 'TP' + str(i), end)
            state['stage'] = stage
            if stage == 3:
                state['status'] = 'TP3'
        if state['status'] == 'ACTIVE' and end >= state['deadline_ms']:
            state['status'] = 'TIMEOUT'
            event(state, 'TIMEOUT_24H', end)
    # Never expire over missing history. Need a contiguous observed timeline.
    if state['status'] == 'PENDING' and state['next_ms'] >= state['expires_ms']:
        state['status'] = 'EXPIRED'
    if state['status'] in LIVE and state['next_ms'] + MINUTE <= now_ms and not candles:
        state['quality'] = 'DATA_GAP'
    return state


class SignalHistory:
    def __init__(self, path=RUNTIME_DIR / 'signal-history.sqlite3'):
        self.path = path
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS tracked_signals (id TEXT PRIMARY KEY, status TEXT, next_ms INTEGER, data TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS signal_status_time ON tracked_signals(status,next_ms)')
            db.execute('CREATE TABLE IF NOT EXISTS history_poll_cursor (pair TEXT PRIMARY KEY, attempted_ms INTEGER)')

    def register(self, envelope, proposal, now=None, reference_id=None, basis="AI"):
        now = now or datetime.now(timezone.utc)
        message = signal_message(envelope, proposal, now)
        if message is None:
            return None
        candidate = envelope['input']['market']['candidate']
        entry, stop = candidate['entry'], candidate['stop_reference']
        issued = int(now.timestamp()*1000)
        observation_start = ((issued + MINUTE - 1)//MINUTE)*MINUTE
        state = {'publication_basis': basis, 'id': reference_id or message['id'], 'packet_id': proposal['packet_id'], 'pair': proposal['pair'],
                 'direction': proposal['action'], 'issued_ms': issued, 'observation_start_ms': observation_start,
                 'expires_ms': millis(proposal['expires_at']), 'entry': entry, 'stop': stop,
                 'current_stop': stop, 'targets': list(map(str, targets(entry, stop, proposal['action']))),
                 'status': 'PENDING', 'stage': 0, 'quality': 'OK', 'next_ms': observation_start,
                 'entry_evidence': {**snapshot(envelope, proposal, now), 'publication_basis': basis},
                 'events': [], 'model': 'MARK_1M_REFERENCE_TOUCH_V1', 'actual_trade_pnl': 'NOT_COMPUTED'}
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('INSERT OR IGNORE INTO tracked_signals VALUES (?,?,?,?)',
                       (state['id'], state['status'], state['next_ms'], json.dumps(state)))
        return state['id']

    def records(self, live_only=False):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            sql = 'SELECT data FROM tracked_signals'
            if live_only:
                sql += " WHERE status IN ('PENDING','ACTIVE')"
            return [json.loads(row[0]) for row in db.execute(sql + ' ORDER BY next_ms,id')]

    def commit(self, before, after):
        # Prevent a slower concurrent updater from overwriting newer progress.
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('UPDATE tracked_signals SET status=?,next_ms=?,data=? WHERE id=? AND next_ms=? AND status=?',
                       (after['status'], after['next_ms'], json.dumps(after), before['id'], before['next_ms'], before['status']))

    def update(self, client, now=None, max_pairs=20):
        now = now or datetime.now(timezone.utc)
        now_ms = int(now.timestamp()*1000)
        grouped = {}
        for state in self.records(live_only=True):
            grouped.setdefault(state['pair'], []).append(state)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            attempts = dict(db.execute('SELECT pair,attempted_ms FROM history_poll_cursor'))
        checked, errors = 0, []
        # One bounded forward window per pair, starting at its oldest unseen minute.
        queue = sorted(grouped.items(), key=lambda item: (attempts.get(item[0], 0), min(s['next_ms'] for s in item[1])))
        for pair, states in queue[:max_pairs]:
            start = min(s['next_ms'] for s in states)
            end = min(now_ms//MINUTE*MINUTE, start + 100*MINUTE)
            if end <= start:
                continue
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.execute('INSERT OR REPLACE INTO history_poll_cursor VALUES (?,?)', (pair, now_ms))
            try:
                rows = client.get('mark_history', instId=pair, bar='1m', after=str(end), limit='100')
                for state in states:
                    if state['next_ms'] >= end:
                        continue
                    after = evaluate(state, rows, min(now_ms, end))
                    # If provider returned only newer history, do not silently jump the gap.
                    if after['next_ms'] < end and after['status'] in LIVE:
                        after['quality'] = 'DATA_GAP'
                    self.commit(state, after)
                    checked += 1
            except Exception as exc:
                errors.append({'pair': pair, 'reason': type(exc).__name__})
                for state in states:
                    self.commit(state, dict(state, quality='DATA_ERROR'))
        return {'checked_signals': checked, 'errors': errors, **self.summary()}

    def summary(self):
        rows = self.records()
        counts = Counter(s['status'] for s in rows)
        milestones = Counter(e['event'] for s in rows for e in s['events'] if e['event'] in ('TP1', 'TP2', 'TP3'))
        return {'scope': 'REFERENCE_SIGNALS_NOT_TRADE_PNL', 'total': len(rows), 'statuses': dict(counts),
                'confirmed_tp_milestones': dict(milestones),
                'data_incomplete': sum(s['quality'] != 'OK' for s in rows),
                'note': 'Mark-price reference touches; no fills, fees, funding, win rate or realized PnL inferred.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true', help='Fetch public mark candles; never submit orders or messages')
    parser.add_argument('--details', action='store_true', help='Include individual reference signal histories')
    parser.add_argument('--db', type=Path, default=RUNTIME_DIR / 'signal-history.sqlite3')
    args = parser.parse_args()
    history = SignalHistory(args.db)
    report = history.update(OKXPublic()) if args.update else history.summary()
    if args.details:
        report['signals'] = history.records()
    output = RUNTIME_DIR / 'signal-history-output.json'
    temporary = output.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(output)
    print(json.dumps(report, indent=2))
    return int(bool(report.get('errors')))


if __name__ == '__main__':
    raise SystemExit(main())
