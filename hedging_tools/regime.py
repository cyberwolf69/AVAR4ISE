"""Descriptive closed-candle context, never a trading permission or probability."""
from hedging_tools.risk_engine import number as D


def describe(rows):
    if len(rows) < 60:
        raise ValueError('Regime needs 60 closed candles')
    closes = [r[3] for r in rows]
    moves = [abs(b-a) for a,b in zip(closes[-21:], closes[-20:])]
    travel = sum(moves)
    efficiency = abs(closes[-1]-closes[-21])/travel if travel else D(0)
    delta = closes[-1]-closes[-21]
    ranges = [max(b[1]-b[2],abs(b[1]-a[3]),abs(b[2]-a[3])) for a,b in zip(rows,rows[1:])]
    recent = sum(ranges[-14:])/14
    previous = sum(ranges[-42:-14])/28
    ratio = recent/previous if previous else None
    state = 'DIRECTIONAL' if efficiency >= D('.4') else 'CHOPPY' if efficiency <= D('.2') else 'MIXED'
    volatility = ('UNKNOWN' if ratio is None else 'EXPANDING' if ratio >= D('1.5')
                  else 'CONTRACTING' if ratio <= D('.75') else 'NORMAL')
    return {'model':'efficiency_atr_context_v1','condition':state,
            'net_direction':'UP' if delta>0 else 'DOWN' if delta<0 else 'FLAT',
            'efficiency_20':str(efficiency),'atr_ratio_14_to_previous_28':str(ratio) if ratio is not None else None,
            'volatility':volatility,'last_closed_bar':rows[-1][0],
            'role':'CONTEXT_ONLY_NOT_ENTRY_GATE','thresholds':'efficiency .2/.4; ATR ratio .75/1.5',
            'limitation':'Descriptive hypothesis, not a calibrated probability or forecast.'}
