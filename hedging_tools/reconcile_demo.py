"""Read-only reconciliation of an existing demo execution journal."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.okx_demo import OKXDemo, DemoError
from hedging_tools.risk_engine import number


FIELDS = ('ordId', 'clOrdId', 'instId', 'side', 'state', 'avgPx', 'accFillSz',
          'fee', 'feeCcy', 'pnl', 'algoId', 'algoClOrdId')


def read_order(client, pair, optional=False, **identity):
    try:
        rows = client.get('order', instId=pair, **identity)
    except DemoError as exc:
        if optional and exc.api_code == '51603':
            return None
        raise
    if optional and not rows:
        return None
    if len(rows) != 1 or rows[0].get('instId') != pair or any(rows[0].get(k) != v for k, v in identity.items()):
        raise DemoError('Order identity mismatch during reconciliation')
    return {key: rows[0].get(key) for key in FIELDS}


def stop_exits(client, report, existing_ids):
    pair, prefix = report['plan']['pair'], report['client_id']
    rows = client.get('algo', algoClOrdId=prefix + 's')
    if len(rows) != 1:
        raise DemoError('Expected exactly one native stop record')
    algo = rows[0]
    side = 'sell' if report['plan'].get('direction', 'LONG') == 'LONG' else 'buy'
    if (algo.get('instId') != pair or algo.get('algoClOrdId') != prefix + 's'
            or not algo.get('algoId') or algo.get('ordType') != 'conditional'
            or algo.get('state') != 'effective' or algo.get('side') != side
            or algo.get('slOrdPx') != '-1' or algo.get('slTriggerPxType') != 'mark'
            or number(algo.get('slTriggerPx') or 0) != number(report['plan']['stop'])):
        raise DemoError('Native stop identity, state or parameters mismatch')
    ids = algo.get('ordIdList')
    if not ids and algo.get('ordId'):
        ids = [algo['ordId']]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
            or any(not isinstance(i, str) or not i.isdigit() for i in ids)
            or len(set(ids)) != len(ids) or set(ids) & existing_ids):
        raise DemoError('Missing, duplicate or ambiguous triggered order IDs')
    exits = []
    for order_id in ids:
        order = read_order(client, pair, ordId=order_id)
        # Require a backlink as well as the parent's list, rather than matching by price/time.
        if (not (order.get('algoId') == algo['algoId'] or order.get('algoClOrdId') == prefix + 's')
                or order.get('algoId') not in (None, '', algo['algoId'])
                or order.get('algoClOrdId') not in (None, '', prefix + 's')):
            raise DemoError('Triggered order does not link back to this stop')
        exits.append(order)
    return exits, {k: algo.get(k) for k in ('algoId', 'algoClOrdId', 'state', 'ordIdList', 'ordId', 'slTriggerPx')}


def reconcile(client, report):
    if report.get("mode") != "OKX_DEMO" or report.get("purpose") not in ("EXECUTION_TEST_NOT_AI_SIGNAL", "AI_SIGNAL_EXECUTION_TEST"):
        raise ValueError("Expected a demo execution-test journal")
    pair, prefix = report["plan"]["pair"], report["client_id"]
    entry = read_order(client, pair, clOrdId=prefix)
    regular_exit = read_order(client, pair, optional=True, clOrdId=prefix + 'x')
    orders = [entry] + ([regular_exit] if regular_exit else [])
    native = None
    if regular_exit is None or number(regular_exit.get('accFillSz') or 0) < number(entry.get('accFillSz') or 0):
        extra, native = stop_exits(client, report, {o['ordId'] for o in orders})
        orders.extend(extra)
    positions = [p for p in client.get("positions", instType="SWAP") if number(p["pos"]) != 0]
    pending = client.get("orders", instType="SWAP")
    algos = []
    for kind in ("conditional", "oco", "trigger", "move_order_stop"):
        algos.extend(client.get("algos", instType="SWAP", ordType=kind))
    balance = client.get("balance", ccy="USDT")
    usdt = next(d for d in balance[0]["details"] if d["ccy"] == "USDT")
    # IOC can finish canceled after a partial fill. Match actual executed size,
    # never the originally requested quantity, and require terminal states.
    filled = all(o["state"] in ("filled", "canceled") for o in orders)
    matched = number(entry['accFillSz'] or 0) == sum(number(o['accFillSz'] or 0) for o in orders[1:]) > 0
    direction = report["plan"].get("direction", "LONG")
    sides = ("buy", "sell") if direction == "LONG" else ("sell", "buy")
    valid_sides = direction in ("LONG", "SHORT") and entry['side'] == sides[0] and all(o['side'] == sides[1] for o in orders[1:])
    fees_known = all(o["feeCcy"] == "USDT" and o["fee"] not in (None, "") for o in orders)
    pnl_known = all(o["pnl"] not in (None, "") for o in orders)
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "orders": orders,
               'native_stop': native, 'exit_source': 'NATIVE_STOP_AND_BOT' if native and regular_exit else 'NATIVE_STOP' if native else 'BOT',
               "entry_exit_filled_and_matched": filled and matched and valid_sides,
               "open_swap_positions": len(positions), "pending_swap_orders": len(pending),
               "pending_swap_algos": len(algos), "usdt_equity": usdt["eq"],
               "usdt_available": usdt["availBal"], "new_orders_sent": 0}
    summary["status"] = "VERIFIED_FLAT" if summary["entry_exit_filled_and_matched"] and not positions and not pending and not algos else "NEEDS_REVIEW"
    if fees_known and pnl_known:
        fees = sum(number(o["fee"]) for o in orders)
        gross = sum(number(o["pnl"]) for o in orders)
        summary.update(gross_trade_pnl_usdt=str(gross), signed_fees_usdt=str(fees),
                       net_trade_pnl_excluding_funding_usdt=str(gross + fees))
    else:
        summary["pnl_status"] = "Incomplete order fee/PnL data; not estimated"
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path)
    args = parser.parse_args()
    report = json.loads(args.journal.read_text())
    result = reconcile(OKXDemo(), report)
    report["reconciliation"] = result
    temporary = args.journal.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(args.journal)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "VERIFIED_FLAT")


if __name__ == "__main__":
    raise SystemExit(main())
