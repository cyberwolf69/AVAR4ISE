"""Causal swing/BOS/retest detector. Independent implementation, not LuxAlgo code."""
from hedging_tools.risk_engine import number


def pivots(rows, width=2):
    """Emit only confirmed pivots, with both origin and availability indices."""
    result = []
    for i in range(width, len(rows) - width):
        neighbors = rows[i-width:i] + rows[i+1:i+width+1]
        for kind, column, compare in (('HIGH', 1, max), ('LOW', 2, min)):
            value = rows[i][column]
            other = compare(r[column] for r in neighbors)
            if (kind == 'HIGH' and value > other) or (kind == 'LOW' and value < other):
                result.append({'kind': kind, 'index': i, 'confirmed': i+width,
                               'price': value, 'time': rows[i][0],
                               'confirmed_time': rows[i+width][0]})
    return result


def trend(rows):
    points = pivots(rows)
    highs = [p['price'] for p in points if p['kind'] == 'HIGH']
    lows = [p['price'] for p in points if p['kind'] == 'LOW']
    if min(len(highs), len(lows)) < 2:
        return 0
    if highs[-1] > highs[-2] and lows[-1] > lows[-2] and rows[-1][3] > lows[-1]:
        return 1
    if highs[-1] < highs[-2] and lows[-1] < lows[-2] and rows[-1][3] < highs[-1]:
        return -1
    return 0


def setup(fast, slow, atr, bid, ask):
    direction = trend(slow)
    evidence = {'model': 'structure_bos_retest_v1', 'htf_direction': direction,
                'pivot_confirmation_bars': 2}
    def wait(reason):
        return {'evidence': evidence, 'reason': reason}
    if not direction:
        return wait('STRUCTURE_1H_UNCONFIRMED: need confirmed HH/HL or LH/LL')
    points = pivots(fast)
    breaks = []
    consumed = set()
    kind = 'HIGH' if direction == 1 else 'LOW'
    for i in range(1, len(fast)):
        available = [p for p in points if p['kind'] == kind and p['confirmed'] < i]
        if not available:
            continue
        pivot = available[-1]
        level = pivot['price']
        if pivot['index'] not in consumed and direction * (fast[i-1][3]-level) <= 0 < direction * (fast[i][3]-level):
            consumed.add(pivot['index'])
            breaks.append((i, pivot))
    if not breaks:
        return wait('NO_CONFIRMED_BOS_15M')
    i, pivot = breaks[-1]
    level = pivot['price']
    evidence.update(bos_time=fast[i][0], level=str(level), pivot_time=pivot['time'],
                    pivot_confirmed_time=pivot['confirmed_time'])
    age = len(fast)-1-i
    evidence['bos_age_bars'] = age
    if age == 0:
        return wait('WAIT_RETEST: BOS just confirmed; need a later candle')
    if age > 8:
        return wait('SETUP_EXPIRED: BOS older than 8 bars; waiting for a new structure break')
    tolerance = atr * number('0.25')
    after = fast[i+1:]
    if any(direction*(row[3]-level) < -tolerance for row in after):
        return wait('BROKEN_RETEST_STRUCTURE')
    last = fast[-1]
    touch = last[2] <= level+tolerance and last[1] >= level-tolerance
    if not touch or direction*(last[3]-level) <= 0 or direction*(last[3]-fast[-2][3]) <= 0:
        return wait('WAIT_RETEST_CONFIRMATION: need zone touch and directional close')
    entry = ask if direction == 1 else bid
    if not 0 < direction*(entry-level) <= atr*number('0.75'):
        return wait('ENTRY_OUTSIDE_STRUCTURE_ZONE')
    stop = (min(r[2] for r in after)-atr*number('0.2') if direction == 1
            else max(r[1] for r in after)+atr*number('0.2'))
    if stop <= 0 or direction*(entry-stop) <= 0:
        return wait('INVALID_STRUCTURE_STOP')
    evidence.update(confirmation_time=last[0], invalidation=str(stop))
    return {'evidence': evidence, 'action': 'LONG' if direction == 1 else 'SHORT',
            'entry': entry, 'stop': stop}
