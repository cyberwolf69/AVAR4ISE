"""Select liquid crypto USDT swaps; all turnover comparisons use USDT units."""
from hedging_tools.market_time import fresh_age
import argparse
import json
import re
from datetime import datetime, timezone
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paths import RUNTIME_DIR
from hedging_tools.risk_engine import number as D

MAX_PAIRS = 20
MIN_TURNOVER = D('10000000')
MAX_SPREAD = D('0.001')


def crypto_swap(instrument):
    pair = instrument.get('instId', '')
    return (bool(re.fullmatch(r'[A-Z0-9]+-USDT-SWAP', pair))
            and instrument.get('instCategory') == '1'
            and instrument.get('instType') == 'SWAP'
            and instrument.get('ctType') == 'linear'
            and instrument.get('settleCcy') == 'USDT'
            and instrument.get('ctValCcy') == pair.split('-')[0]
            and instrument.get('state') == 'live')


def rank(instruments, tickers, now=None, limit=MAX_PAIRS):
    if not 1 <= limit <= MAX_PAIRS:
        raise ValueError('Universe limit must be 1..20')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone required')
    allowed = {i['instId']: i for i in instruments if crypto_swap(i)}
    rows, rejected, seen = [], {}, set()
    for ticker in tickers:
        pair = ticker.get('instId', '')
        if pair not in allowed or pair in seen:
            continue
        seen.add(pair)
        try:
            instrument = allowed[pair]
            last, bid, ask, high, low, volume = (D(ticker[k]) for k in ('last', 'bidPx', 'askPx', 'high24h', 'low24h', 'volCcy24h'))
            if not fresh_age(now.timestamp() - int(ticker['ts']) / 1000, 30):
                raise ValueError('STALE_TICKER')
            if not 0 < bid <= ask or min(last, low, volume) <= 0 or high < max(last, low):
                raise ValueError('INVALID_MARKET_VALUES')
            if D(instrument['lever']) < 10 or min(D(instrument[k]) for k in ('ctVal', 'ctMult', 'lotSz', 'minSz', 'tickSz')) <= 0:
                raise ValueError('UNSUPPORTED_CONTRACT')
            # OKX derivatives volCcy24h is BASE units, not quote units or contracts.
            turnover = volume * last
            spread = (ask - bid) / bid
            if turnover < MIN_TURNOVER:
                raise ValueError('LOW_TURNOVER')
            if spread > MAX_SPREAD:
                raise ValueError('WIDE_SPREAD')
            rows.append({'pair': pair, 'turnover_usdt_estimate': turnover,
                         'spread_fraction': spread, 'range_fraction': (high-low)/last})
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else 'INVALID_DATA'
            rejected[reason] = rejected.get(reason, 0) + 1
    maximum = max((r['turnover_usdt_estimate'] for r in rows), default=D(1))
    for row in rows:
        liquidity = (row['turnover_usdt_estimate'] / maximum).sqrt()
        tightness = max(D(0), 1 - row['spread_fraction'] / MAX_SPREAD)
        activity = min(D(1), row['range_fraction'] / D('0.10'))
        row['score'] = D('.60')*liquidity + D('.20')*tightness + D('.20')*activity*liquidity
    rows.sort(key=lambda r: (-r['score'], r['pair']))
    selected = [{k: str(v) if not isinstance(v, str) else v for k, v in row.items()} for row in rows[:limit]]
    return {'status': 'READY' if selected else 'EMPTY', 'as_of': now.isoformat(),
            'pairs': [r['pair'] for r in selected], 'selected': selected,
            'eligible_count': len(rows), 'rejected': rejected, 'max_pairs': limit,
            'min_turnover_usdt': str(MIN_TURNOVER), 'max_spread_fraction': str(MAX_SPREAD)}


def select(client, now=None):
    try:
        instruments = client.get('instruments', instType='SWAP')
        tickers = client.get('tickers', instType='SWAP')
        return rank(instruments, tickers, now)
    except Exception:
        # No stale cache fallback: discovery failure cannot authorize new pairs.
        return {'status': 'UNAVAILABLE', 'pairs': [], 'selected': [], 'reason': 'Universe market data unavailable'}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = select(OKXPublic())
    (RUNTIME_DIR / 'universe-output.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return int(report['status'] != 'READY')
