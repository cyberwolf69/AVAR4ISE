"""Deterministic screening baseline; scores are not win probabilities."""
from hedging_tools.market_time import fresh_age
from datetime import datetime, timezone
from decimal import ROUND_FLOOR
from hedging_tools.risk_engine import number
from hedging_tools.structure import setup
from hedging_tools.regime import describe


def closed_candles(rows, seconds, now_ms):
    candles = []
    for row in rows:
        if len(row) != 9 or row[8] not in ("0", "1"):
            raise ValueError("Invalid candle schema")
        if row[8] == "0":
            continue
        timestamp = int(row[0])
        o, h, low, close, volume = (number(row[i]) for i in (1, 2, 3, 4, 7))
        if min(o, h, low, close) <= 0 or volume < 0 or not low <= min(o, close) <= max(o, close) <= h:
            raise ValueError("Invalid OHLCV")
        if timestamp % (seconds * 1000) or timestamp + seconds * 1000 > now_ms:
            raise ValueError("Misaligned or future closed candle")
        candles.append((timestamp, h, low, close, volume))
    candles.sort()
    if len(candles) < 60:
        raise ValueError("At least 60 closed candles required")
    if any(b[0] - a[0] != seconds * 1000 for a, b in zip(candles, candles[1:])):
        raise ValueError("Duplicate or missing candle")
    age = now_ms - (candles[-1][0] + seconds * 1000)
    if age > seconds * 1000 + 30_000:
        raise ValueError("Stale candles")
    return candles


def ema(values, period):
    alpha = number(2) / (period + 1)
    value = values[0]
    for item in values[1:]:
        value += alpha * (item - value)
    return value


def features(candles):
    closes = [row[3] for row in candles]
    ranges = [max(b[1] - b[2], abs(b[1] - a[3]), abs(b[2] - a[3]))
              for a, b in zip(candles, candles[1:])]
    atr = sum(ranges[-14:]) / 14
    average_volume = sum(row[4] for row in candles[-21:-1]) / 20
    if average_volume <= 0 or atr <= 0:
        raise ValueError("Insufficient volume or volatility")
    fast, slow = ema(closes, 20), ema(closes, 50)
    direction = 1 if closes[-1] > fast > slow else -1 if closes[-1] < fast < slow else 0
    return {"direction": direction, "atr": atr, "close": closes[-1],
            "ema20": fast, "ema50": slow, "rvol": candles[-1][4] / average_volume}


def screen(snapshot, config, now=None):
    now = now or datetime.now(timezone.utc)
    result = {"pair": snapshot.get("pair"), "observed_at": now.isoformat(),
              "status": "WAIT", "execution": "DISABLED", "reasons": [],
              "news_status": "NOT_CONNECTED", "ai_status": "NOT_CONNECTED"}
    try:
        _screen(snapshot, config, now, result)
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        result["status"] = "DATA_ERROR"
        result["reasons"].append(str(exc))
    return result


def _screen(s, c, now, out):
    if now.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    pair = s["pair"]
    if pair not in c["pairs"]:
        raise ValueError("Pair not allowed")
    instrument, ticker = s["instrument"], s["ticker"]
    for key in ("instrument", "ticker", "funding", "interest"):
        if s[key]["instId"] != pair:
            raise ValueError("Instrument identity mismatch")
    base = pair.split("-")[0]
    if (instrument["ctType"] != "linear" or instrument["settleCcy"] != "USDT"
            or instrument["state"] != "live" or instrument["ctValCcy"] != base):
        raise ValueError("Unsupported instrument")
    now_ms = int(now.timestamp() * 1000)
    for key in ("ticker", "funding", "interest"):
        age = (now_ms - int(s[key]["ts"])) / 1000
        limit = c["max_market_age_seconds"] if key == "ticker" else 300
        if not fresh_age(age, limit):
            raise ValueError(f"Stale or future {key}: age={age:.3f}s, allowed=-1.000..{limit}s")
    bid, ask = number(ticker["bidPx"]), number(ticker["askPx"])
    if not 0 < bid <= ask:
        raise ValueError("Invalid bid/ask")
    spread = (ask - bid) / bid
    fast_rows = closed_candles(s["candles_15m"], 900, now_ms)
    slow_rows = closed_candles(s["candles_1h"], 3600, now_ms)
    fast, slow = features(fast_rows), features(slow_rows)
    funding, oi = number(s["funding"]["fundingRate"]), number(s["interest"]["oi"])
    if oi <= 0:
        raise ValueError("Invalid open interest")
    out["metrics"] = {"spread_fraction": str(spread), "funding_rate": str(funding),
                      "funding_time": s["funding"]["fundingTime"], "open_interest_contracts": str(oi),
                      "15m": {k: str(v) for k, v in fast.items()},
                      "1h": {k: str(v) for k, v in slow.items()}}
    out["metrics"]["regime"] = {"15m": describe(fast_rows), "1h": describe(slow_rows)}
    if spread > number(c["max_spread_fraction"]):
        out["reasons"].append("Spread exceeds limit")
    model = c.get("setup_model", "ema_baseline")
    if model == "structure_bos_retest_v1":
        structural = setup(fast_rows, slow_rows, fast['atr'], bid, ask)
        out['metrics']['structure'] = structural['evidence']
        if structural.get('reason'):
            out['reasons'].append(structural['reason'])
        if out['reasons']:
            return
        side, entry, stop = structural['action'], structural['entry'], structural['stop']
        distance = abs(entry-stop)
    elif model == "ema_baseline":
        if fast["direction"] == 0 or fast["direction"] != slow["direction"]:
            out["reasons"].append("15m and 1h trends are not aligned")
        if fast["rvol"] < 1:
            out["reasons"].append("Closed-candle volume below previous 20-bar average")
        if out["reasons"]:
            return
        side = "LONG" if fast["direction"] > 0 else "SHORT"
        entry = ask if side == "LONG" else bid
        distance = fast["atr"] * number("1.5")
        stop = entry - distance if side == "LONG" else entry + distance
    else:
        raise ValueError("Unknown setup model")
    if stop <= 0:
        raise ValueError("Invalid stop")
    unit = number(instrument["ctVal"]) * number(instrument["ctMult"])
    lot, minimum = number(instrument["lotSz"]), number(instrument["minSz"])
    if min(unit, lot, minimum) <= 0:
        raise ValueError("Invalid contract sizing")
    choices = []
    for leverage in range(c["min_leverage"], min(c["max_leverage"], int(number(instrument["lever"]))) + 1):
        quantity = (number(c["margin_per_trade"]) * leverage / (entry * unit) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        notional = quantity * unit * entry
        risk = quantity * unit * distance + notional * number(c["estimated_roundtrip_cost_fraction"])
        if quantity >= minimum and risk <= number(c["risk_per_trade"]):
            choices.append({"leverage": leverage, "contracts": str(quantity),
                            "margin": str(notional / leverage), "planned_risk": str(risk)})
    if not choices:
        out["reasons"].append("No contract size fits $5 margin and $1 planned risk")
        return
    out.update(status="WATCHLIST", candidate={"setup_model": model, "action": side, "entry": str(entry),
               "stop_reference": str(stop), "sizing_options": choices},
               reasons=["Setup passed; requires AI review and full account risk gate; news is optional"])
