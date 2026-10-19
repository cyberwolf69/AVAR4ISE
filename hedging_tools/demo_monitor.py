"""Bounded demo position supervision; order mutations stay in the executor."""
import time
from hedging_tools.okx_demo import DemoError
from hedging_tools.risk_engine import number


def position_limits(position, plan, unit, risk_budget):
    sign = 1 if plan['direction'] == 'LONG' else -1
    size, entry, stop = number(position['pos']), number(position['avgPx']), number(plan['stop'])
    leverage = number(plan.get('leverage', '10'))
    if (position['instId'] != plan['pair'] or position['mgnMode'] != 'isolated'
            or size * sign <= 0 or abs(size) > number(plan['size'])
            or not 10 <= leverage <= 30 or number(position['lever']) != leverage
            or not 0 < number(position['margin']) <= 5):
        raise DemoError('Position differs from allowed size, margin, direction or leverage')
    distance = (entry - stop) * sign
    liquidation = number(position.get('liqPx') or 0)
    # Keep the stop at least one fifth of the entry-to-stop distance from liquidation.
    if entry <= 0 or distance <= 0 or liquidation <= 0 or (stop - liquidation) * sign < distance * number('.2'):
        raise DemoError('Stop or reported liquidation buffer is not safe to hold')
    costs = abs(size) * unit * entry * number('.002')
    if abs(size) * unit * distance + costs > number(risk_budget):
        raise DemoError('Actual fill exceeds the remaining planned risk budget')
    return {'target': entry + sign * 2 * distance, 'entry': entry,
            'size': abs(size), 'stop': stop, 'sign': sign, 'costs': costs}


def monitor(client, public, plan, prefix, unit, risk_budget, seconds, record,
            monotonic=time.monotonic, wall=time.time, sleep=time.sleep, expires_at=None,
            stop_requested=lambda: False):
    if not isinstance(seconds, int) or not 1 <= seconds <= 300:
        raise ValueError('Holding window must be 1..300 seconds')
    deadline = monotonic() + seconds
    while True:
        if stop_requested():
            return 'STOP_REQUESTED'
        if monotonic() >= deadline:
            return 'TIME_LIMIT'
        if expires_at is not None and wall() >= expires_at:
            return 'SIGNAL_EXPIRED'
        positions = [p for p in client.get('positions', instId=plan['pair']) if number(p['pos'])]
        if not positions:
            return 'POSITION_CLOSED_ON_EXCHANGE'
        if len(positions) != 1:
            raise DemoError('Unexpected multiple positions')
        limits = position_limits(positions[0], plan, unit, risk_budget)
        algos = client.get('algos', instId=plan['pair'], ordType='conditional')
        stops = [a for a in algos if a.get('algoClOrdId') == prefix + 's']
        if (len(stops) != 1 or stops[0].get('state') != 'live'
                or number(stops[0]['slTriggerPx']) != limits['stop']
                or stops[0].get('slOrdPx') != '-1' or stops[0].get('slTriggerPxType') != 'mark'):
            raise DemoError('Expected live native mark stop is missing or changed')
        marks = public.get('mark', instType='SWAP', instId=plan['pair'])
        if len(marks) != 1 or marks[0]['instId'] != plan['pair'] or not 0 <= wall() - int(marks[0]['ts']) / 1000 <= 30:
            raise DemoError('Missing or stale mark price during supervision')
        mark = number(marks[0]['markPx'])
        if mark <= 0:
            raise DemoError('Invalid mark price')
        gross = (mark - limits['entry']) * limits['sign'] * limits['size'] * unit
        record('monitor', {'observed_at_unix': wall(), 'mark': str(mark),
                          'target': str(limits['target']), 'gross_unrealized': str(gross)})
        if (mark - limits['stop']) * limits['sign'] <= 0:
            return 'STOP_THRESHOLD'
        if gross - limits['costs'] <= -number(risk_budget):
            return 'RISK_LIMIT'
        if (mark - limits['target']) * limits['sign'] >= 0:
            return 'TAKE_PROFIT_2R'
        sleep(max(0, min(5, deadline - monotonic())))
