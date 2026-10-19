"""One read-only OKX refresh -> validated AI proposal -> local paper ledger."""
from hedging_tools.paths import CONFIG_PATH
import argparse
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.analyst import validate_proposal
from hedging_tools.okx_public import OKXPublic, MarketDataError
from hedging_tools.paper import PaperLedger
from hedging_tools.risk_engine import number

from hedging_tools.paths import RUNTIME_DIR as ROOT


def refresh_market(client, pair):
    def one(endpoint, **params):
        rows = client.get(endpoint, instId=pair, **params)
        if len(rows) != 1 or rows[0].get("instId") != pair:
            raise ValueError("Missing or mismatched market response")
        return rows[0]
    instrument = one("instruments", instType="SWAP")
    mark = one("mark", instType="SWAP")
    ticker = one("ticker")
    if instrument["ctValCcy"] != pair.split("-")[0]:
        raise ValueError("Unsupported contract value currency")
    observed = min(int(ticker["ts"]), int(mark["ts"]))
    return {"pair": pair, "observed_at": datetime.fromtimestamp(observed / 1000, timezone.utc).isoformat(),
            "bid": ticker["bidPx"], "ask": ticker["askPx"], "mark": mark["markPx"],
            "contract_type": instrument["ctType"], "settle_currency": instrument["settleCcy"],
            "state": instrument["state"], "base_units_per_contract": str(number(instrument["ctVal"]) * number(instrument["ctMult"])),
            "lot_size": instrument["lotSz"], "min_contracts": instrument["minSz"],
            "max_leverage": int(number(instrument["lever"])),
            # Do not invent a venue liquidation estimate from leverage alone.
            "estimated_liquidation_by_leverage": {}}


def apply_proposal(ledger, proposal, envelope, market, now):
    signal = validate_proposal(proposal, envelope, now)["signal"]
    if market["pair"] != signal["pair"]:
        raise ValueError("Proposal and refreshed quote pair mismatch")
    position = ledger.snapshot()["position"]
    if position and position["pair"] != signal["pair"]:
        return {"action": "WAIT", "reason": "Another pair already occupies the single position slot", "execution": "SIMULATED_ONLY"}
    return ledger.process({"id": str(uuid.uuid4()), "proposal_id": proposal["packet_id"],
                           "at": now.isoformat(), "type": "signal", "market": market, "signal": signal})


def load_latest(database):
    with closing(sqlite3.connect(database)) as db, db:
        latest = db.execute("SELECT run_id FROM analyst_packets ORDER BY rowid DESC LIMIT 1").fetchone()
        if not latest:
            raise ValueError("No research packets available")
        rows = db.execute("SELECT p.envelope_json,a.result_json FROM analyst_packets p JOIN ai_analyses a ON a.packet_id=p.packet_id AND a.run_id=p.run_id WHERE p.run_id=? ORDER BY p.rowid", latest).fetchall()
    return latest[0], [(json.loads(e), json.loads(a)) for e, a in rows]


def cycle(client, ledger, records, clock=lambda: datetime.now(timezone.utc)):
    results = []
    position = ledger.snapshot()["position"]
    if position:
        # Monitor existing risk even when all AI proposals are absent or expired.
        market = refresh_market(client, position["pair"])
        results.append(ledger.process({"id": str(uuid.uuid4()), "at": clock().isoformat(), "type": "mark", "market": market}))
    for envelope, analysis in records:
        pair = envelope["input"]["pair"]
        try:
            if analysis["status"] != "VALIDATED":
                results.append({"pair": pair, "action": "SKIP", "reason": "AI proposal not validated"})
                continue
            # Validate once before API calls, again against the actual submission time.
            validate_proposal(analysis["proposal"], envelope, clock())
            market = refresh_market(client, pair)
            result = apply_proposal(ledger, analysis["proposal"], envelope, market, clock())
            results.append({"pair": pair, **result})
        except (ValueError, KeyError, TypeError, ArithmeticError, MarketDataError) as exc:
            results.append({"pair": pair, "action": "REJECT", "reason": str(exc), "execution": "SIMULATED_ONLY"})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, default=ROOT / "scans.sqlite3")
    parser.add_argument("--paper-db", type=Path, default=ROOT / "paper-live.sqlite3")
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text())
    ledger = PaperLedger(args.paper_db, config)
    run_id, records = load_latest(args.research_db)
    results = cycle(OKXPublic(), ledger, records)
    report = {"run_id": run_id, "mode": "PAPER_ONLY", "results": results,
              "state": ledger.snapshot(), "limitation": "Directional entry requires verified liquidation estimates; absent estimates are rejected"}
    with closing(sqlite3.connect(args.paper_db)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS paper_bridge_runs (id INTEGER PRIMARY KEY, report_json TEXT)")
        db.execute("INSERT INTO paper_bridge_runs (report_json) VALUES (?)", (json.dumps(report),))
    (ROOT / "paper-bridge-output.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(any(r["action"] == "REJECT" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
