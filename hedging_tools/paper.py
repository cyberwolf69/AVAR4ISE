"""Offline event-driven paper ledger. No network or exchange execution."""
from hedging_tools.paths import CONFIG_PATH
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from hedging_tools.risk_engine import RiskEngine, number

from hedging_tools.paths import RUNTIME_DIR as ROOT


class PaperLedger:
    def __init__(self, path, config):
        self.path, self.config = path, config
        self.risk = RiskEngine(config)
        self.zone = ZoneInfo(config["timezone"])
        self.fee = number("0.0005")  # Explicit simulation assumptions, per side.
        self.slippage = number("0.0002")
        self.fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS paper_state (id INTEGER PRIMARY KEY CHECK(id=1), config_hash TEXT, state_json TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_events (event_id TEXT PRIMARY KEY, input_json TEXT, result_json TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_proposals (proposal_id TEXT PRIMARY KEY, signal_json TEXT, result_json TEXT)")
            db.execute("INSERT OR IGNORE INTO paper_state VALUES (1,?,?)", (self.fingerprint, json.dumps({
                "cash": config["initial_equity"], "equity": config["initial_equity"],
                "day_start_equity": config["initial_equity"], "day": None,
                "halted_today": False, "position": None, "last_at": None,
                "fees_paid": "0", "funding_net": "0", "realized_gross": "0"})))
            self._load(db)

    def _load(self, db):
        fingerprint, encoded = db.execute("SELECT config_hash,state_json FROM paper_state WHERE id=1").fetchone()
        if fingerprint != self.fingerprint:
            raise ValueError("Ledger config differs; use a new simulation database")
        return json.loads(encoded)

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return self._load(db)

    def _equity(self, state, mark):
        p = state["position"]
        unrealized = number(0) if not p else number(p["units"]) * (mark - number(p["entry"])) * p["sign"]
        state["equity"] = str(number(state["cash"]) + unrealized)

    def _close(self, state, bid, ask, reason):
        p = state["position"]
        price = bid * (1 - self.slippage) if p["sign"] == 1 else ask * (1 + self.slippage)
        units = number(p["units"])
        gross, fee = units * (price - number(p["entry"])) * p["sign"], units * price * self.fee
        state["cash"] = str(number(state["cash"]) + gross - fee)
        state["fees_paid"] = str(number(state["fees_paid"]) + fee)
        state["realized_gross"] = str(number(state["realized_gross"]) + gross)
        state["position"] = None
        state["equity"] = state["cash"]
        return {"action": "CLOSE_PAPER", "reason": reason, "fill": str(price), "gross_pnl": str(gross), "exit_fee": str(fee)}

    def process(self, event):
        encoded = json.dumps(event, sort_keys=True)
        if not isinstance(event.get("id"), str) or not event["id"]:
            raise ValueError("Unique event ID required")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT input_json,result_json FROM paper_events WHERE event_id=?", (event["id"],)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValueError("Event ID reused with different content")
                return json.loads(old[1])
            proposal_id = event.get("proposal_id")
            signal_json = json.dumps(event.get("signal"), sort_keys=True)
            if proposal_id:
                previous = db.execute("SELECT signal_json,result_json FROM paper_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
                if previous:
                    if previous[0] != signal_json:
                        raise ValueError("Proposal reused with a different signal")
                    return {"action": "SKIP_ALREADY_PROCESSED", "proposal_id": proposal_id,
                            "state": self._load(db), "execution": "SIMULATED_ONLY"}
            state = self._load(db)
            result = self._process(state, event)
            result.update(state=state, execution="SIMULATED_ONLY")
            db.execute("UPDATE paper_state SET state_json=? WHERE id=1", (json.dumps(state),))
            db.execute("INSERT INTO paper_events VALUES (?,?,?)", (event["id"], encoded, json.dumps(result)))
            if proposal_id:
                db.execute("INSERT INTO paper_proposals VALUES (?,?,?)", (proposal_id, signal_json, json.dumps(result)))
            return result

    def _process(self, s, event):
        now = datetime.fromisoformat(event["at"])
        if now.tzinfo is None:
            raise ValueError("Timezone-aware event time required")
        if s["last_at"] and now < datetime.fromisoformat(s["last_at"]):
            raise ValueError("Out-of-order event")
        market = event["market"]
        pair = market["pair"]
        if pair not in self.config["pairs"] or (s["position"] and s["position"]["pair"] != pair):
            raise ValueError("Quote must match the open position or allowed pair")
        age = (now - datetime.fromisoformat(market["observed_at"])).total_seconds()
        if not 0 <= age <= self.config["max_market_age_seconds"]:
            raise ValueError("Stale or future quote")
        bid, ask, mark = (number(market[k]) for k in ("bid", "ask", "mark"))
        if not 0 < bid <= ask or mark <= 0:
            raise ValueError("Invalid quote")
        kind = event["type"]
        if kind not in ("mark", "signal", "close", "funding"):
            raise ValueError("Unknown event type")
        self._equity(s, mark)
        local = now.astimezone(self.zone)
        day = local.date().isoformat()
        if day != s["day"]:
            if s["day"] and s["position"] and (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
                raise ValueError("Open position requires a midnight WIB quote before next-day events")
            s.update(day=day, day_start_equity=s["equity"], halted_today=False)
        if kind == "funding":
            rate = number(event["rate"])
            settlement = datetime.fromisoformat(event["settlement_at"])
            if settlement != now:
                raise ValueError("Replay funding at its settlement time")
            settlement_key = pair + ":" + settlement.astimezone(timezone.utc).isoformat()
            if settlement_key in s.get("funding_settlements", []):
                raise ValueError("Funding settlement already applied")
            s.setdefault("funding_settlements", []).append(settlement_key)
            if s["position"]:
                payment = -s["position"]["sign"] * number(s["position"]["units"]) * mark * rate
                s["cash"] = str(number(s["cash"]) + payment)
                s["funding_net"] = str(number(s["funding_net"]) + payment)
                self._equity(s, mark)
        limit = number(s["day_start_equity"]) * (1 - number(self.config["daily_loss_fraction"]))
        result = {"action": "MARK_PAPER"}
        if number(s["equity"]) <= limit or s["halted_today"]:
            s["halted_today"] = True
            result = self._close(s, bid, ask, "DAILY_LOSS_LIMIT") if s["position"] else {"action": "HALT"}
        elif s["position"]:
            p = s["position"]
            stop_hit = bid <= number(p["stop"]) if p["sign"] == 1 else ask >= number(p["stop"])
            if stop_hit or kind == "close":
                result = self._close(s, bid, ask, "STOP_LOSS" if stop_hit else "EXPLICIT_EXIT")
            elif kind == "signal":
                result = {"action": "WAIT", "reason": "One position already open"}
        elif kind == "signal":
            signal = event["signal"]
            # Account inputs come only from this ledger, never from an AI proposal.
            account = {"day_start_equity": s["day_start_equity"], "equity": s["equity"],
                       "available_balance": s["cash"], "halted_today": s["halted_today"],
                       "open_positions": 0, "pending_entry": False}
            # Include adverse entry slippage before sizing; use original spread gate.
            adjusted = dict(market)
            if signal["action"] in ("LONG", "SHORT"):
                if (ask - bid) / bid > number(self.config["max_spread_fraction"]):
                    s["last_at"] = now.isoformat()
                    return {"action": "WAIT", "reason": "Spread exceeds limit"}
                adjusted["ask"] = str(ask * (1 + self.slippage))
                adjusted["bid"] = str(bid * (1 - self.slippage))
            decision = self.risk.evaluate(signal, adjusted, account, now)
            result = {"action": decision.action, "risk_decision": asdict(decision)}
            if decision.action == "APPROVE_PAPER":
                sign = 1 if signal["action"] == "LONG" else -1
                entry = number(adjusted["ask"] if sign == 1 else adjusted["bid"])
                units = number(decision.contracts) * number(market["base_units_per_contract"])
                fee = entry * units * self.fee
                s["cash"] = str(number(s["cash"]) - fee)
                s["fees_paid"] = str(number(s["fees_paid"]) + fee)
                s["position"] = {"pair": pair, "sign": sign, "entry": str(entry),
                                 "units": str(units), "stop": signal["stop"],
                                 "margin": decision.margin, "leverage": decision.leverage,
                                 "opened_at": now.isoformat()}
                self._equity(s, mark)
                result["action"] = "OPEN_PAPER"
        if number(s["equity"]) <= limit:
            s["halted_today"] = True
            if s["position"]:
                result = self._close(s, bid, ask, "DAILY_LOSS_LIMIT")
        s["last_at"] = now.isoformat()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="JSON array of timestamped offline events")
    parser.add_argument("--db", type=Path, default=ROOT / "paper.sqlite3")
    args = parser.parse_args()
    ledger = PaperLedger(args.db, json.loads(CONFIG_PATH.read_text()))
    for event in json.loads(args.events.read_text()):
        print(json.dumps(ledger.process(event)))


if __name__ == "__main__":
    main()
