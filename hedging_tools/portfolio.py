"""Durable OKX Demo portfolio: native split TPs, supervised SL, no blind entry retry."""
from hedging_tools.market_time import fresh_age
import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from hedging_tools.analyst import validate_proposal
from hedging_tools.demo_execution import accepted, plan
from hedging_tools.okx_demo import DemoError
from hedging_tools.position_policy import MAX_POSITIONS, COST_BUFFER, split_size, targets, protective_stop
from hedging_tools.risk_engine import number as D
from hedging_tools.universe import crypto_swap
from hedging_tools.position_policy import PAIRS
from hedging_tools.improve_evidence import snapshot, order_evidence

TERMINAL = ('filled', 'canceled')


class MissingAlgo(DemoError):
    pass


class Store:
    def __init__(self, path, legacy=None):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS portfolio (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT)')
            if db.execute('SELECT 1 FROM portfolio').fetchone():
                return
            old = legacy or {}
            if old.get('pending'):
                raise ValueError('Reconcile legacy pending attempt before starting portfolio')
            initial = old.get('equity', '50')
            state = {'initial': initial, 'equity': initial, 'day': old.get('day'),
                     'day_start': old.get('day_start_equity', initial), 'halted': old.get('halted', False),
                     'positions': [], 'funding': {}, 'funding_checked': int(time.time() * 1000),
                     'consumed': [], 'last_run': None}
            db.execute('INSERT INTO portfolio VALUES (1,?)', (json.dumps(state),))

    def read(self):
        with closing(sqlite3.connect(self.path)) as db:
            return json.loads(db.execute('SELECT data FROM portfolio WHERE id=1').fetchone()[0])

    def save(self, state):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE portfolio SET data=? WHERE id=1', (json.dumps(state),))


def active(state):
    return [p for p in state['positions'] if p['status'] != 'CLOSED']


def order_pnl(order):
    if D(order.get('accFillSz') or 0) == 0:
        return D(0)
    if order.get('feeCcy') != 'USDT' or order.get('fee') in (None, '') or order.get('pnl') in (None, ''):
        raise DemoError('Missing USDT fee/PnL evidence')
    return D(order['fee']) + D(order['pnl'])


def refresh_budget(state, now):
    realized = sum((D(p.get('pnl', '0')) for p in state['positions']), D(0))
    funding = sum((D(value) for value in state['funding'].values()), D(0))
    upl = sum((D(p.get('upl', '0')) for p in active(state)), D(0))
    equity = D(state['initial']) + realized + funding + upl
    day = now.astimezone(ZoneInfo('Asia/Jakarta')).date().isoformat()
    if state['day'] != day:
        # Conservative restart/midnight baseline: never erase an unobserved loss.
        state.update(day=day, day_start=str(max(D(state['equity']), equity)), halted=False)
    state['equity'] = str(equity)
    if equity <= D(state['day_start']) * D('0.9'):
        state['halted'] = True
    return equity


def entry_allowance(state, pair, exchange_positions, orders):
    if state['halted']:
        return D(0), 'DAILY_LOSS_LIMIT'
    ours = active(state)
    exchange_pairs = {p['instId'] for p in exchange_positions if D(p['pos'])}
    reserved_pairs = {p['pair'] for p in ours}
    if len(exchange_pairs | reserved_pairs) >= MAX_POSITIONS:
        return D(0), 'POSITION_SLOTS_FULL'
    if pair in exchange_pairs | reserved_pairs:
        return D(0), 'PAIR_ALREADY_OPEN'
    if orders or exchange_pairs - reserved_pairs:
        return D(0), 'UNMANAGED_EXPOSURE_OR_PENDING_ORDER'
    if any(p['status'] != 'OPEN' for p in ours):
        return D(0), 'UNRECONCILED_POSITION'
    available = D(state['equity']) - D(state['day_start']) * D('0.9')
    # Reserve further loss from current mark to stop, including exit cost buffer.
    reserved_risk = sum((D(p['remaining_risk']) for p in ours), D(0))
    reserved_margin = sum((D(p['margin']) for p in ours), D(0))
    if D(state['equity']) - reserved_margin < 6:
        return D(0), 'STRATEGY_MARGIN_LIMIT'
    return max(D(0), min(D(1), available - reserved_risk)), 'AGGREGATE_RISK_LIMIT'


class Portfolio:
    def __init__(self, store, client, public, clock=None):
        self.store, self.client, self.public = store, client, public
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def save(self, state):
        self.store.save(state)

    def get_order(self, pair, **identity):
        rows = self.client.get('order', instId=pair, **identity)
        if len(rows) != 1 or rows[0].get('instId') != pair:
            raise DemoError('Missing or mismatched order evidence')
        return rows[0]

    def algo_orders(self, p, suffix):
        try:
            rows = self.client.get('algo', algoClOrdId=p['id'] + suffix)
        except DemoError as exc:
            if exc.api_code == '51603':
                raise MissingAlgo('Native TP/SL not found') from None
            raise
        if not rows:
            raise MissingAlgo('Native TP/SL not found')
        if len(rows) != 1:
            raise DemoError('Ambiguous native TP/SL evidence')
        algo = rows[0]
        if algo.get('instId') != p['pair'] or algo.get('algoClOrdId') != p['id'] + suffix:
            raise DemoError('Native TP/SL identity mismatch')
        ids = algo.get('ordIdList') or ([algo['ordId']] if algo.get('ordId') else [])
        if not isinstance(ids, list) or len(ids) > 20 or len(set(ids)) != len(ids):
            raise DemoError('Invalid algo child order IDs')
        orders = []
        for oid in ids:
            if not str(oid).isdigit():
                raise DemoError('Invalid algo child order ID')
            row = self.get_order(p['pair'], ordId=oid)
            if row.get('algoId') != algo['algoId'] and row.get('algoClOrdId') != algo['algoClOrdId']:
                raise DemoError('Unlinked exit order')
            orders.append(row)
        return algo, orders

    def emergency_exit(self, state, p, quantity, reason):
        # Record intent BEFORE mutation. An ambiguous response is only reconciled, never resent.
        if not p.get('emergency'):
            p['emergency'] = p['id'] + 'x'
            p['error'] = reason
            p['protective_exit_reason'] = reason
            self.save(state)
            accepted(self.client.post('order', {'instId': p['pair'], 'tdMode': 'isolated',
                'posSide': 'net', 'side': 'sell' if p['direction'] == 'LONG' else 'buy',
                'ordType': 'market', 'sz': str(quantity), 'reduceOnly': True, 'clOrdId': p['emergency']}))
        raise DemoError('Protective exit pending reconciliation: ' + reason)

    def reconcile_one(self, state, p):
        entry = self.get_order(p['pair'], clOrdId=p['id'])
        if entry.get('clOrdId') != p['id'] or entry.get('side') != ('buy' if p['direction'] == 'LONG' else 'sell'):
            raise DemoError('Entry identity mismatch')
        if entry['state'] not in TERMINAL:
            raise DemoError('Entry not terminal yet')
        filled = D(entry['accFillSz'])
        if filled == 0:
            p.update(status='CLOSED', pnl='0', upl='0', closed_at=int(entry['uTime']), exit_kind='NO_FILL',
                     execution_evidence=[order_evidence(entry, 'ENTRY')])
            return
        if filled != D(p['size']):
            raise DemoError('Unexpected partial FOK fill; manual reconciliation required')
        p['opened_at'] = int(entry['cTime'])
        p['entry'] = entry['avgPx']
        positions = [row for row in self.client.get('positions', instId=p['pair']) if D(row['pos'])]
        if len(positions) > 1:
            raise DemoError('Unexpected multiple position records')
        row = positions[0] if positions else None
        quantity = abs(D(row['pos'])) if row else D(0)
        sign = 1 if p['direction'] == 'LONG' else -1
        if row and (row.get('mgnMode') != 'isolated' or D(row['pos']) * sign <= 0 or quantity > filled
                    or D(row['avgPx']) != D(entry['avgPx'])
                    or not row.get('posId') or p.get('position_id', row['posId']) != row['posId']):
            raise DemoError('Position ownership/size mismatch; manual review required')
        if row:
            p['position_id'] = row['posId']
        # Read the stop first so a missing/failed protection can be handled even if a TP is absent.
        try:
            stop_algo, stop_orders = self.algo_orders(p, 's')
        except MissingAlgo:
            if quantity and not p.get('emergency'):
                return self.emergency_exit(state, p, quantity, 'Missing native stop')
            if not p.get('emergency'):
                raise
            stop_algo, stop_orders = {'state': 'missing'}, []
        orders = [entry] + stop_orders
        evidence = [order_evidence(entry, 'ENTRY')] + [order_evidence(o, 'SL') for o in stop_orders]
        completed = []
        broken_tp = False
        for index, part in enumerate(p['legs'], 1):
            try:
                algo, children = self.algo_orders(p, 't' + str(index))
            except MissingAlgo:
                if not p.get('emergency'):
                    if quantity:
                        return self.emergency_exit(state, p, quantity, 'Missing native TP')
                    raise
                algo, children = {'state': 'missing'}, []
            if algo['state'] == 'live' and (D(algo.get('tpTriggerPx') or 0) != D(part['price'])
                    or algo.get('tpOrdPx') != '-1' or algo.get('tpTriggerPxType') != 'mark'):
                raise DemoError('Native TP parameters changed')
            total = sum((D(o.get('accFillSz') or 0) for o in children), D(0))
            done = total == D(part['size']) and all(o['state'] in TERMINAL for o in children)
            completed.append(done)
            if algo['state'] not in ('live', 'effective') and not done:
                broken_tp = True
            orders.extend(children)
            evidence.extend(order_evidence(o, 'TP'+str(index)) for o in children)
        if p.get('emergency'):
            emergency = self.get_order(p['pair'], clOrdId=p['emergency'])
            orders.append(emergency)
            evidence.append(order_evidence(emergency, 'PROTECTIVE_EXIT'))
        ids = [o['ordId'] for o in orders]
        if len(set(ids)) != len(ids):
            raise DemoError('Duplicate execution evidence')
        exit_side = 'sell' if sign > 0 else 'buy'
        if any(o.get('side') != exit_side or o.get('tdMode') != 'isolated' for o in orders[1:]):
            raise DemoError('Exit side/margin mode mismatch')
        exited = sum((D(o.get('accFillSz') or 0) for o in orders[1:]), D(0))
        if filled - exited != quantity or exited > filled:
            raise DemoError('Position differs from owned fills; wait for consistent evidence')
        p['execution_evidence'] = evidence
        p['pnl'] = str(sum((order_pnl(o) for o in orders), D(0)))
        p['upl'] = str(D(row['upl'])) if row else '0'
        if not quantity:
            if any(o['state'] not in TERMINAL for o in orders):
                raise DemoError('Exit order still pending')
            for algo in self.client.get('algos', instId=p['pair'], ordType='conditional'):
                if algo.get('algoClOrdId') in {p['id'] + suffix for suffix in ('s', 't1', 't2', 't3')}:
                    accepted(self.client.post('cancel_algos', [{'instId': p['pair'], 'algoId': algo['algoId']}]))
            if any(a.get('algoClOrdId') in {p['id'] + suffix for suffix in ('s', 't1', 't2', 't3')}
                   for a in self.client.get('algos', instId=p['pair'], ordType='conditional')):
                raise DemoError('Native exits not yet canceled')
            stopped = any(D(o.get('accFillSz') or 0) > 0 for o in stop_orders)
            kind = ('PROTECTIVE_EXIT' if p.get('emergency') else
                    'PROTECTED_STOP' if stopped and any(completed) else
                    'SL' if stopped else 'TP3' if all(completed) else 'OTHER_CLOSED')
            p.update(status='CLOSED', closed_at=max(int(o['uTime']) for o in orders), remaining='0', exit_kind=kind,
                     exit_stop_trigger=stop_algo.get('slTriggerPx'))
            return
        if p.get('emergency'):
            raise DemoError('Protective exit incomplete; manual reconciliation required')
        if broken_tp:
            return self.emergency_exit(state, p, quantity, 'Native TP canceled or failed')
        if stop_algo.get('state') != 'live':
            if any(o['state'] not in TERMINAL for o in stop_orders):
                raise DemoError('Native stop execution in progress')
            return self.emergency_exit(state, p, quantity, 'Native stop not live')
        if (stop_algo.get('slOrdPx') != '-1' or stop_algo.get('slTriggerPxType') != 'mark'
                or D(stop_algo.get('sz') or 0) < quantity):
            return self.emergency_exit(state, p, quantity, 'Native stop parameters changed')
        margin, mark, liquidation = (D(row[k]) for k in ('margin', 'markPx', 'liqPx'))
        if min(margin, mark, liquidation) <= 0 or margin > 5 or D(row['lever']) != D(p['leverage']):
            return self.emergency_exit(state, p, quantity, 'Margin/leverage limit')
        current_stop = D(stop_algo['slTriggerPx'])
        if current_stop <= 0 or (mark - current_stop) * sign <= 0:
            return self.emergency_exit(state, p, quantity, 'Mark crossed live stop')
        original_distance = (D(p['entry']) - D(p['stop'])) * sign
        if D(p['entry']) <= 0 or original_distance <= 0:
            return self.emergency_exit(state, p, quantity, 'Actual fill crossed original stop')
        original_risk = filled * D(p['unit']) * (original_distance + D(p['entry']) * COST_BUFFER)
        if original_risk > D(p['risk_budget']):
            return self.emergency_exit(state, p, quantity, 'Actual fill exceeds reserved risk')
        if (current_stop - liquidation) * sign < original_distance * D('0.2'):
            return self.emergency_exit(state, p, quantity, 'Insufficient liquidation buffer')
        stage = 2 if completed[0] and completed[1] else 1 if completed[0] else 0
        desired = protective_stop(p['entry'], p['stop'], p['direction'], stage, p['tick'])
        if (current_stop - D(p['stop'])) * sign < 0:
            return self.emergency_exit(state, p, quantity, 'Stop loosened externally')
        if (desired - current_stop) * sign > 0:
            if (mark - desired) * sign <= 0:
                return self.emergency_exit(state, p, quantity, 'Price already crossed new protective stop')
            accepted(self.client.post('amend_algo', {'instId': p['pair'], 'algoId': stop_algo['algoId'],
                'newSlTriggerPx': str(desired), 'newSlOrdPx': '-1', 'newSlTriggerPxType': 'mark', 'cxlOnFail': False}))
            # Acknowledgment alone is not a confirmed amendment.
            confirmed, _ = self.algo_orders(p, 's')
            if confirmed.get('state') != 'live' or D(confirmed['slTriggerPx']) != desired:
                raise DemoError('Stop amendment not confirmed yet')
            p.setdefault('stop_history', []).append({'confirmed_at': self.clock().isoformat(),
                                                    'from': str(current_stop), 'to': str(desired), 'tp_stage': stage})
            current_stop = desired
        risk = quantity * D(p['unit']) * (max(D(0), (mark - current_stop) * sign) + mark * COST_BUFFER)
        p.update(status='OPEN', stage=stage, current_stop=str(current_stop), remaining=str(quantity),
                 margin=str(margin), remaining_risk=str(risk))
        p.pop('error', None)

    def funding(self, state):
        now_ms = int(self.clock().timestamp() * 1000)
        # API exposes seven days. Longer outages require explicit reconciliation, never silently skip.
        if now_ms - state['funding_checked'] > 6 * 86400000:
            raise DemoError('Funding history gap requires manual reconciliation')
        begin = max(0, state['funding_checked'] - 86400000)
        params = {'instType': 'SWAP', 'type': '8', 'begin': str(begin), 'end': str(now_ms), 'limit': '100'}
        seen_pages = set()
        for _ in range(100):
            rows = self.client.get('bills', **params)
            for bill in rows:
                ts = int(bill['ts'])
                matching = [p for p in state['positions'] if p['pair'] == bill['instId']
                            and p.get('opened_at', now_ms + 1) <= ts <= p.get('closed_at', now_ms)]
                if matching:
                    if bill['ccy'] != 'USDT' or str(bill['type']) != '8':
                        raise DemoError('Invalid funding bill')
                    state['funding'][bill['billId']] = str(D(bill['balChg']))
                    if len(matching) == 1:
                        matching[0].setdefault('funding_bills', {})[bill['billId']] = str(D(bill['balChg']))
            if len(rows) < 100:
                state['funding_checked'] = now_ms
                return
            cursor = rows[-1]['billId']
            if cursor in seen_pages:
                break
            seen_pages.add(cursor)
            params['after'] = cursor
        raise DemoError('Incomplete funding pagination')

    def manage(self):
        state = self.store.read()
        errors = []
        for p in active(state):
            try:
                self.reconcile_one(state, p)
            except Exception as exc:
                # Continue protecting other positions even when one cannot be reconciled.
                p['error'] = str(exc) if isinstance(exc, (DemoError, ValueError)) else type(exc).__name__
                errors.append({'pair': p['pair'], 'reason': p['error']})
            self.save(state)
        try:
            self.funding(state)
        except Exception as exc:
            errors.append({'reason': str(exc) if isinstance(exc, DemoError) else type(exc).__name__})
        # Partial/old snapshots may latch a halt, but never clear one or reset a day.
        if not errors:
            refresh_budget(state, self.clock())
        else:
            equity = D(state['initial']) + sum((D(p.get('pnl', 0)) + D(p.get('upl', 0)) for p in state['positions']), D(0)) + sum(map(D, state['funding'].values()), D(0))
            if equity <= D(state['day_start']) * D('0.9'):
                state['halted'] = True
        state['errors'] = errors
        self.save(state)
        return {'status': 'MANAGING' if not errors else 'ENTRY_BLOCKED', 'active_positions': len(active(state)),
                'max_positions': MAX_POSITIONS, 'equity': state['equity'], 'daily_halted': state['halted'], 'errors': errors}

    def enter(self, envelope, analysis):
        if analysis.get('status') != 'VALIDATED':
            return {'status': 'WAIT', 'reason': analysis.get('reason', analysis.get('status', 'INVALID_ANALYSIS')),
                    'screen_reasons': analysis.get('screen_reasons', [])}
        validated = validate_proposal(analysis['proposal'], envelope, self.clock())
        signal = validated['signal']
        if signal['action'] == 'WAIT':
            return {'status': 'WAIT', 'reason': analysis['proposal']['reason']}
        # Always refresh all exposure immediately before deciding to add risk.
        self.manage()
        state = self.store.read()
        if state.get('errors'):
            return {'status': 'WAIT', 'reason': 'RECONCILIATION_REQUIRED'}
        packet_id = analysis['proposal']['packet_id']
        if packet_id in state['consumed']:
            return {'status': 'WAIT', 'reason': 'PROPOSAL_ALREADY_CONSUMED'}
        if self.client.get('config')[0].get('posMode') != 'net_mode':
            raise DemoError('Portfolio requires net position mode')
        positions = self.client.get('positions', instType='SWAP')
        pending = self.client.get('orders', instType='SWAP')
        limit, reason = entry_allowance(state, signal['pair'], positions, pending)
        if limit <= 0:
            return {'status': 'WAIT', 'reason': reason}
        own_ids = {p['id'] + suffix for p in active(state) for suffix in ('s', 't1', 't2', 't3')}
        for kind in ('conditional', 'oco', 'trigger', 'move_order_stop'):
            if any(a.get('algoClOrdId') not in own_ids for a in self.client.get('algos', instType='SWAP', ordType=kind)):
                return {'status': 'WAIT', 'reason': 'UNMANAGED_ALGO_ORDER'}
        balance = self.client.get('balance', ccy='USDT')[0]
        if D(next(d['availBal'] for d in balance['details'] if d['ccy'] == 'USDT')) < 6:
            return {'status': 'WAIT', 'reason': 'INSUFFICIENT_EXCHANGE_BALANCE'}
        instrument = self.client.get('instruments', instType='SWAP', instId=signal['pair'])[0]
        if signal['pair'] not in PAIRS and not crypto_swap(instrument):
            return {'status': 'WAIT', 'reason': 'UNSUPPORTED_DEMO_CRYPTO_SWAP'}
        ticker = self.public.get('ticker', instId=signal['pair'])[0]
        if not fresh_age(self.clock().timestamp() - int(ticker['ts']) / 1000, 30):
            raise DemoError('Stale entry quote')
        try:
            details = plan(instrument, ticker, signal, str(limit), allowed_pairs=(signal['pair'],))
            sizes = split_size(details['size'], instrument['lotSz'], instrument['minSz'])
            prices = targets(details['limit_price'], details['stop'], details['direction'], instrument['tickSz'])
        except ValueError as exc:
            return {'status': 'WAIT', 'reason': str(exc)}
        prefix = 'hp' + uuid.uuid4().hex[:20]
        p = dict(details, id=prefix, status='ENTRY_PENDING', pnl='0', upl='0',
                 tick=instrument['tickSz'], unit=str(D(instrument['ctVal']) * D(instrument['ctMult'])),
                 legs=[{'size': str(sz), 'price': str(px)} for sz, px in zip(sizes, prices)])
        p['entry_evidence'] = snapshot(envelope, analysis['proposal'], self.clock(), ticker)
        p['entry_evidence']['risk_context'] = {k: state[k] for k in ('equity', 'day_start', 'day', 'halted')}
        p['entry_evidence']['risk_context'].update(active_positions=len(active(state)), available_risk=str(limit))
        accepted(self.client.post('leverage', {'instId': p['pair'], 'lever': p['leverage'], 'mgnMode': 'isolated'}))
        if self.clock() >= datetime.fromisoformat(signal['expires_at']) or not fresh_age(self.clock().timestamp() - int(ticker['ts']) / 1000, 30):
            return {'status': 'WAIT', 'reason': 'SIGNAL_OR_QUOTE_EXPIRED'}
        # No forced example trades: every entry needs a fresh validated directional proposal.
        state['positions'].append(p)
        state['consumed'].append(packet_id)
        self.save(state)
        attachments = [{'attachAlgoClOrdId': prefix + 't' + str(i), 'tpTriggerPx': leg['price'],
                        'tpOrdPx': '-1', 'tpTriggerPxType': 'mark', 'sz': leg['size']}
                       for i, leg in enumerate(p['legs'], 1)]
        attachments.append({'attachAlgoClOrdId': prefix + 's', 'slTriggerPx': p['stop'],
                            'slOrdPx': '-1', 'slTriggerPxType': 'mark'})
        accepted(self.client.post('order', {'instId': p['pair'], 'tdMode': 'isolated', 'posSide': 'net',
            'side': 'buy' if p['direction'] == 'LONG' else 'sell', 'ordType': 'fok',
            'sz': p['size'], 'px': p['limit_price'], 'clOrdId': prefix, 'attachAlgoOrds': attachments}))
        supervision = self.manage()
        actual = next(item for item in self.store.read()['positions'] if item['id'] == prefix)
        return {'status': actual['status'], 'pair': p['pair'], 'client_id': prefix, 'supervision': supervision['status']}
