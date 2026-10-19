"""One-shot OKX DEMO execution test: IOC entry with SL, then reduce-only exit."""
from hedging_tools.market_time import fresh_age
import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
from hedging_tools.okx_demo import OKXDemo, DemoError
from hedging_tools.okx_public import OKXPublic
from hedging_tools.risk_engine import number
from hedging_tools.demo_monitor import monitor
from hedging_tools.position_policy import PAIRS

from hedging_tools.paths import RUNTIME_DIR as ROOT


class NoTrade(ValueError):
    """Execution declined before any order mutation."""


def plan(instrument, ticker, signal=None, risk_budget="1", allowed_pairs=PAIRS, margin="5", min_leverage=10, max_leverage=30):
    if not (type(min_leverage) is int and type(max_leverage) is int and 10 <= min_leverage <= max_leverage <= 30 and 1 <= number(margin) <= 5):
        raise ValueError("Settings exceed hard risk limits")
    pair = signal["pair"] if signal else "BTC-USDT-SWAP"
    direction = signal["action"] if signal else "LONG"
    if direction not in ("LONG", "SHORT") or pair not in allowed_pairs:
        raise ValueError("Unsupported signal")
    if instrument["instId"] != pair or ticker["instId"] != pair or instrument["ctType"] != "linear" or instrument["settleCcy"] != "USDT" or instrument["ctValCcy"] != pair.split("-")[0] or instrument["state"] != "live":
        raise ValueError("Unsupported instrument")
    bid, ask = number(ticker["bidPx"]), number(ticker["askPx"])
    if not 0 < bid <= ask or (ask - bid) / bid > number("0.001"):
        raise ValueError("Invalid or wide quote")
    unit = number(instrument["ctVal"]) * number(instrument["ctMult"])
    tick, lot, minimum = (number(instrument[k]) for k in ("tickSz", "lotSz", "minSz"))
    if min(unit, tick, lot, minimum) <= 0 or number(instrument["lever"]) < 10:
        raise ValueError("Invalid size/leverage constraints")
    long = direction == "LONG"
    price = ((ask * number("1.001") if long else bid * number("0.999")) / tick).to_integral_value(rounding=ROUND_FLOOR if long else ROUND_CEILING) * tick
    raw_stop = number(signal["stop"]) if signal else bid * number("0.995")
    stop = (raw_stop / tick).to_integral_value(rounding=ROUND_FLOOR if long else ROUND_CEILING) * tick
    sizing_price = max(price, ask * number("1.001"))
    valid_stop = 0 < stop < bid if long else stop > ask
    if not valid_stop or number(risk_budget) <= 0:
        raise ValueError("No trade fits demo test limits")
    # Experiment: fit a maximum $5 margin to the stop-based risk cap, after lot rounding.
    # Keep the independent technical smoke test at 10x.
    cap = min(max_leverage, int(number(instrument['lever']))) if signal else 10
    for leverage in range(cap, (min_leverage if signal else 10)-1, -1):
        quantity = (number(margin) * leverage / (sizing_price * unit) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        notional = quantity * unit * sizing_price
        risk = quantity * unit * abs(price - stop) + notional * number("0.002")
        if quantity < minimum or risk > min(number(1), number(risk_budget)):
            continue
        return {"pair": pair, "size": str(quantity), "limit_price": str(price), "stop": str(stop),
                "direction": direction, "leverage": str(leverage), "margin_upper_bound": str(notional / leverage),
                "planned_risk": str(risk), "risk_budget": str(min(number(1), number(risk_budget))),
                "leverage_policy": "STOP_RISK_CAPPED_10_30" if signal else "TECHNICAL_TEST_10X"}
    raise ValueError("No trade fits demo test limits")


def accepted(rows):
    if not rows or any(str(row.get("sCode", "0")) != "0" for row in rows):
        codes = [str(r.get("sCode", "unknown")) for r in rows]
        raise DemoError("Order rejected: " + ",".join(c if c.isdigit() else "unknown" for c in codes))
    return rows


def run(client, public, journal, signal=None, risk_budget="1", hold_seconds=0, stop_requested=lambda: False):
    if not isinstance(hold_seconds, int) or not 0 <= hold_seconds <= 300:
        raise ValueError('Holding window must be 0..300 seconds')
    prefix = "ht" + uuid.uuid4().hex[:18]
    report = {"mode": "OKX_DEMO", "purpose": "AI_SIGNAL_EXECUTION_TEST" if signal else "EXECUTION_TEST_NOT_AI_SIGNAL", "client_id": prefix,
              "started_at": datetime.now(timezone.utc).isoformat(), "hold_seconds": hold_seconds, "steps": [], "status": "PREPARING"}
    def save():
        temporary = journal.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(journal)
    def record(action, data):
        report["steps"].append({"action": action, "data": data})
        save()
    pair = signal["pair"] if signal else "BTC-USDT-SWAP"
    config = client.get("config")[0]
    if config.get("posMode") != "net_mode":
        raise DemoError("Test requires existing net mode; not changing account mode")
    if any(number(p["pos"]) != 0 for p in client.get("positions", instType="SWAP")) or client.get("orders", instType="SWAP"):
        raise DemoError("Account must have no SWAP positions or pending orders")
    for kind in ("conditional", "oco", "trigger", "move_order_stop"):
        if client.get("algos", instType="SWAP", ordType=kind):
            raise DemoError("Existing algo orders; test stopped before mutations")
    balance = client.get("balance", ccy="USDT")[0]
    usdt = next(d for d in balance["details"] if d["ccy"] == "USDT")
    if number(usdt["availBal"]) < 6:
        raise DemoError("Insufficient available demo USDT")
    rows = client.get("instruments", instType="SWAP", instId=pair)
    if len(rows) != 1:
        raise DemoError("Missing demo instrument")
    ticker = public.get("ticker", instId=pair)[0]
    if not fresh_age(time.time() - int(ticker["ts"]) / 1000, 30):
        raise DemoError("Stale quote")
    if signal and datetime.now(timezone.utc) >= datetime.fromisoformat(signal["expires_at"]):
        raise DemoError("Signal expired before order placement")
    try:
        details = plan(rows[0], ticker, signal, risk_budget)
    except ValueError as exc:
        report.update(status='WAIT', reason=str(exc), orders_sent=0)
        save()
        raise NoTrade(str(exc)) from None
    accepted(client.post("leverage", {"instId": pair, "lever": details['leverage'], "mgnMode": "isolated"}))
    if not fresh_age(time.time() - int(ticker['ts']) / 1000, 30):
        raise DemoError('Quote expired while setting leverage')
    if signal and datetime.now(timezone.utc) >= datetime.fromisoformat(signal['expires_at']):
        raise DemoError('Signal expired while setting leverage')
    report["plan"] = details
    report["status"] = "ENTRY_PREPARED"
    save()  # Persist client ID before network mutation.
    if stop_requested():
        report.update(status='WAIT', reason='Stop requested before entry', orders_sent=0)
        save()
        raise NoTrade('Stop requested before entry')
    entry = {"instId": pair, "tdMode": "isolated", "side": "buy" if details["direction"] == "LONG" else "sell", "posSide": "net",
             "ordType": "ioc", "sz": details["size"], "px": details["limit_price"], "clOrdId": prefix,
             "attachAlgoOrds": [{"attachAlgoClOrdId": prefix + "s", "slTriggerPx": details["stop"],
                                  "slOrdPx": "-1", "slTriggerPxType": "mark"}]}
    error = None
    try:
        record("entry_ack", accepted(client.post("order", entry)))
        order = None
        for _ in range(6):
            order = client.get("order", instId=pair, clOrdId=prefix)[0]
            if order["state"] in ("filled", "canceled"):
                break
            time.sleep(.5)
        if order is None:
            raise DemoError("Entry state unknown")
        record("entry_state", {k: order.get(k) for k in ("ordId", "state", "avgPx", "accFillSz", "fee", "feeCcy", "attachAlgoOrds")})
        if order["state"] not in ("filled", "canceled") or number(order.get("accFillSz") or 0) <= 0:
            raise DemoError("Entry did not reach a confirmed fill")
        positions = [p for p in client.get("positions", instId=pair) if number(p["pos"]) != 0]
        record("position_check", [{k: p.get(k) for k in ("instId", "pos", "avgPx", "lever", "mgnMode", "liqPx", "margin")} for p in positions])
        algos = client.get("algos", instId=pair, ordType="conditional")
        ours = [a for a in algos if a.get("algoClOrdId") == prefix + "s"]
        record("stop_check", [{k: a.get(k) for k in ("algoId", "algoClOrdId", "slTriggerPx", "slOrdPx", "state")} for a in ours])
        report["stop_confirmed"] = bool(ours)
        if positions and not ours:
            raise DemoError("Attached stop not observed; closing immediately")
        if hold_seconds:
            report['status'] = 'MONITORING'
            save()
            report['exit_reason'] = monitor(client, public, details, prefix,
                number(rows[0]['ctVal']) * number(rows[0]['ctMult']), risk_budget,
                hold_seconds, record,
                stop_requested=stop_requested,
                expires_at=datetime.fromisoformat(signal['expires_at']).timestamp() if signal else None)
            save()
        else:
            report['exit_reason'] = 'IMMEDIATE_EXECUTION_TEST'
    except Exception as exc:
        error = str(exc) if isinstance(exc, DemoError) else type(exc).__name__
    finally:
        # Reconcile first; never blindly resend an ambiguous entry.
        pending = [o for o in client.get("orders", instId=pair) if o.get("clOrdId") == prefix]
        for order in pending:
            record("cancel_entry", accepted(client.post("cancel", {"instId": pair, "ordId": order["ordId"]})))
        for p in client.get("positions", instId=pair):
            quantity = number(p["pos"])
            if quantity:
                expected_sign = 1 if details["direction"] == "LONG" else -1
                if p.get("mgnMode") != "isolated" or quantity * expected_sign < 0:
                    raise DemoError("Unexpected position: manual reconciliation required")
                record("exit_ack", accepted(client.post("order", {"instId": pair, "tdMode": "isolated", "posSide": "net",
                       "side": "sell" if quantity > 0 else "buy", "ordType": "market", "sz": str(abs(quantity)), "reduceOnly": True, "clOrdId": prefix + "x"})))
        for _ in range(6):
            positions = [p for p in client.get("positions", instId=pair) if number(p["pos"]) != 0]
            if not positions:
                break
            time.sleep(.5)
        if positions:
            report["status"] = "NEEDS_RECONCILIATION"
            save()
            raise DemoError("Demo position still open; reconcile before any new entry")
        for a in client.get("algos", instId=pair, ordType="conditional"):
            if a.get("algoClOrdId") == prefix + "s":
                record("cancel_stop", accepted(client.post("cancel_algos", [{"instId": pair, "algoId": a["algoId"]}])))
        report["final_open_positions"] = len(positions)
        report["final_pending_orders"] = len(client.get("orders", instId=pair))
        report["final_own_stops"] = len([a for a in client.get("algos", instId=pair, ordType="conditional") if a.get("algoClOrdId") == prefix + "s"])
        report["status"] = "CLOSED_WITH_TEST_ERROR" if error else "COMPLETE"
        if error:
            report["error"] = error
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo-test", action="store_true", required=True)
    parser.parse_args()
    journal = ROOT / ("demo-execution-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json")
    try:
        result = run(OKXDemo(), OKXPublic(), journal)
        print(json.dumps(result, indent=2))
        return int(result["status"] != "COMPLETE")
    except Exception as exc:
        print(json.dumps({"status": "STOPPED_CHECK_JOURNAL", "error": str(exc) if isinstance(exc, (DemoError, ValueError)) else type(exc).__name__, "journal": str(journal)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
