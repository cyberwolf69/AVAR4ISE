"""Shared price/quantity rules for the portfolio and signal cards."""
from decimal import ROUND_FLOOR, ROUND_CEILING
from hedging_tools.risk_engine import number as D

MAX_POSITIONS = 5
PAIRS = ('BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP', 'XRP-USDT-SWAP', 'DOGE-USDT-SWAP')
COST_BUFFER = D('0.002')
BRAND_URL = 'https://www.x.com/web3rai'
BRAND_TEXT = '𝕏 web3rai'


def targets(entry, stop, direction, tick=None):
    entry, stop = D(entry), D(stop)
    sign = 1 if direction == 'LONG' else -1
    distance = (entry - stop) * sign
    if direction not in ('LONG', 'SHORT') or min(entry, stop, distance) <= 0:
        raise ValueError('Invalid entry/stop')
    prices = [entry + sign * distance * r for r in (1, 2, 3)]
    if tick is not None:
        prices = [round_price(p, tick, sign) for p in prices]
    if min(prices) <= 0 or len(set(prices)) != 3:
        raise ValueError('Invalid TP ladder')
    return prices


def round_price(price, tick, sign):
    return (D(price) / D(tick)).to_integral_value(rounding=ROUND_CEILING if sign > 0 else ROUND_FLOOR) * D(tick)


def split_size(size, lot, minimum):
    size, lot, minimum = D(size), D(lot), D(minimum)
    if min(size, lot, minimum) <= 0 or size % lot:
        raise ValueError('Invalid contract size')
    first, second = [(size * D(weight) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot for weight in ('0.40', '0.35')]
    parts = [first, second, size - first - second]
    if min(parts) < minimum:
        raise ValueError('Size too small for three exchange-valid TP legs')
    return parts


def protective_stop(entry, original_stop, direction, stage, tick):
    sign = 1 if direction == 'LONG' else -1
    entry, original_stop = D(entry), D(original_stop)
    if stage == 0:
        return original_stop
    # Estimated fees/slippage buffer; not a guarantee of net break-even.
    price = entry * (1 + sign * COST_BUFFER)
    if stage >= 2:
        one_r = entry + (entry - original_stop)
        price = max(price, one_r) if sign > 0 else min(price, one_r)
    return round_price(price, tick, sign)
