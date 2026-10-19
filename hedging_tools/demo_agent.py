"""One bounded AI-signal execution test on OKX Demo, with durable risk budget."""
from hedging_tools.paths import PROJECT_ROOT
import argparse
import fcntl
import json
import math
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from hedging_tools.analyst import validate_proposal
from hedging_tools.demo_execution import run as execute, NoTrade
from hedging_tools.okx_demo import OKXDemo, DemoError
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paper_bridge import load_latest
from hedging_tools.risk_engine import number
from hedging_tools.reconcile_demo import reconcile

from hedging_tools.paths import RUNTIME_DIR as ROOT
TRADE_COOLDOWN_SECONDS = 900


def cooldown_seconds(state, now):
    deadline = state.get('next_entry_after')
    return max(0, math.ceil((datetime.fromisoformat(deadline) - now).total_seconds())) if deadline else 0


class Budget:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS strategy_budget (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS consumed_proposals (packet_id TEXT PRIMARY KEY)")
            db.execute('CREATE TABLE IF NOT EXISTS verified_settlements (journal TEXT PRIMARY KEY, settled_at TEXT, net_change TEXT, evidence_json TEXT)')
            db.execute("INSERT OR IGNORE INTO strategy_budget VALUES (1,?)", (json.dumps({"equity": "50", "day_start_equity": "50", "day": None, "pending": None, "halted": False}),))

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            return json.loads(db.execute("SELECT state FROM strategy_budget WHERE id=1").fetchone()[0])

    def state(self, now):
        with closing(sqlite3.connect(self.path)) as db, db:
            s = json.loads(db.execute("SELECT state FROM strategy_budget WHERE id=1").fetchone()[0])
            day = now.astimezone(ZoneInfo("Asia/Jakarta")).date().isoformat()
            if s["pending"]:
                raise ValueError("Unreconciled demo attempt; review journal before any new trade")
            if s["day"] != day:
                # Strategy is flat between cycles: carried equity is the midnight baseline.
                s.update(day=day, day_start_equity=s["equity"], halted=False)
                db.execute("UPDATE strategy_budget SET state=? WHERE id=1", (json.dumps(s),))
            return s

    def reserve(self, packet_id, journal, before, now):
        self.state(now)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            s = json.loads(db.execute("SELECT state FROM strategy_budget WHERE id=1").fetchone()[0])
            if s["pending"]:
                raise ValueError("Another demo attempt already reserved")
            if cooldown_seconds(s, now):
                raise ValueError('Post-trade cooldown is still active')
            if db.execute("SELECT 1 FROM consumed_proposals WHERE packet_id=?", (packet_id,)).fetchone():
                raise ValueError("Proposal already consumed")
            remaining = number(s["equity"]) - number(s["day_start_equity"]) * number("0.9")
            if s["halted"] or remaining <= 0 or number(s["equity"]) < 6:
                raise ValueError("Strategy daily loss or remaining-equity limit")
            s["pending"] = {"journal": str(journal), "account_equity_before": str(before), "reserved_at": now.isoformat()}
            db.execute("UPDATE strategy_budget SET state=? WHERE id=1", (json.dumps(s),))
            db.execute("INSERT INTO consumed_proposals VALUES (?)", (packet_id,))
            return str(min(number(1), remaining))

    def settle(self, after, expected_journal=None, recovery=None, verification=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            s = json.loads(db.execute("SELECT state FROM strategy_budget WHERE id=1").fetchone()[0])
            if not s["pending"]:
                raise ValueError("No pending attempt")
            if expected_journal is not None and s['pending']['journal'] != str(expected_journal):
                raise ValueError("Pending journal changed; refusing settlement")
            delta = number(after) - number(s["pending"]["account_equity_before"])
            s["equity"] = str(number(s["equity"]) + delta)
            s["halted"] = s["halted"] or number(s["equity"]) <= number(s["day_start_equity"]) * number("0.9")
            s["last_net_change"] = str(delta)
            if recovery is not None:
                s['last_recovery'] = recovery
            evidence = verification if verification is not None else recovery
            if evidence is not None:
                settled_at = datetime.now(timezone.utc)
                db.execute('INSERT INTO verified_settlements VALUES (?,?,?,?)',
                           (s['pending']['journal'], settled_at.isoformat(), str(delta), json.dumps(evidence)))
                s['last_verification'] = evidence
                s['next_entry_after'] = (settled_at + timedelta(seconds=TRADE_COOLDOWN_SECONDS)).isoformat()
            s["pending"] = None
            db.execute("UPDATE strategy_budget SET state=? WHERE id=1", (json.dumps(s),))
            return s


def verify_settlement(state, result, now):
    if not state['pending']:
        raise ValueError('No pending attempt to verify')
    if now.astimezone(ZoneInfo('Asia/Jakarta')).date().isoformat() != state['day']:
        raise ValueError('Attempt crosses a daily boundary; manual budget review required')
    if result['status'] != 'VERIFIED_FLAT':
        raise ValueError('Settlement requires matched fills and no remaining exposure')
    pnl = result.get('net_trade_pnl_excluding_funding_usdt')
    delta = number(result['usdt_equity']) - number(state['pending']['account_equity_before'])
    if pnl is None or abs(delta - number(pnl)) > number('0.000001'):
        raise ValueError('Account change does not match trade PnL; manual budget review required')


def recover(budget, client_factory=OKXDemo, now=None):
    """Read exchange state only; unlock solely a proven, fully closed round trip."""
    state = budget.snapshot()
    pending = state['pending']
    if pending is None:
        return {'status': 'NO_PENDING_ATTEMPT', 'orders_sent': 0, 'budget': state}
    now = now or datetime.now(timezone.utc)
    if now.astimezone(ZoneInfo('Asia/Jakarta')).date().isoformat() != state['day']:
        raise ValueError('Attempt crosses a daily boundary; manual budget review required')
    journal = Path(pending['journal'])
    report = json.loads(journal.read_text())
    if report.get('purpose') != 'AI_SIGNAL_EXECUTION_TEST':
        raise ValueError('Pending journal is not an AI demo attempt')
    result = reconcile(client_factory(), report)
    verify_settlement(state, result, now)
    # Evidence and settlement are committed together; rerunning cannot charge twice.
    settled = budget.settle(result['usdt_equity'], expected_journal=journal,
                            recovery={'journal': str(journal), 'reconciliation': result})
    return {'status': 'RECOVERED', 'orders_sent': 0, 'budget': settled}


def equity(client):
    rows = client.get("balance", ccy="USDT")
    return number(next(d["eq"] for d in rows[0]["details"] if d["ccy"] == "USDT"))


def select(records, now):
    for envelope, analysis in records:
        if analysis.get("status") != "VALIDATED":
            continue
        validated = validate_proposal(analysis["proposal"], envelope, now)
        if validated["signal"]["action"] in ("LONG", "SHORT"):
            return analysis["proposal"]["packet_id"], validated["signal"]
    return None


def once(hold_seconds=0, stop_requested=lambda: False):
    if not isinstance(hold_seconds, int) or not 0 <= hold_seconds <= 300:
        raise ValueError('Holding window must be 0..300 seconds')
    if (ROOT / 'portfolio.sqlite3').exists():
        raise ValueError('Portfolio budget is active; use python3 main.py run instead of legacy agent')
    budget = Budget(ROOT / "demo-strategy.sqlite3")
    state = budget.state(datetime.now(timezone.utc))
    if state["halted"]:
        return {"status": "DAILY_HALT", "budget": state, "orders_sent": 0}
    delay = cooldown_seconds(state, datetime.now(timezone.utc))
    if delay:
        return {'status': 'COOLDOWN', 'retry_after_seconds': delay, 'budget': state, 'orders_sent': 0}
    # Explicit research call; no scheduled loop or background process.
    completed = subprocess.run([sys.executable, "-B", "-m", "hedging_tools.research", "--ai", "--db", str(ROOT / "scans.sqlite3"), "--output", str(ROOT / "research-output.json")], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300)
    if completed.returncode:
        return {"status": "RESEARCH_FAILED", "orders_sent": 0, "details": "See research-output.json; no execution attempted"}
    run_id, records = load_latest(ROOT / "scans.sqlite3")
    if stop_requested():
        return {'status': 'STOPPED', 'orders_sent': 0, 'budget': state}
    chosen = select(records, datetime.now(timezone.utc))
    if chosen is None:
        return {"status": "WAIT", "run_id": run_id, "reason": "No validated directional AI proposal", "budget": state, "orders_sent": 0}
    packet_id, signal = chosen
    client = OKXDemo()
    # Executor rechecks this immediately before entry as well.
    if any(number(p["pos"]) for p in client.get("positions", instType="SWAP")) or client.get("orders", instType="SWAP"):
        raise DemoError("Existing SWAP exposure; no automated test")
    journal = ROOT / ("demo-execution-" + uuid.uuid4().hex + ".json")
    before = equity(client)
    limit = budget.reserve(packet_id, journal, before, datetime.now(timezone.utc))
    try:
        result = execute(client, OKXPublic(), journal, signal=signal, risk_budget=limit, hold_seconds=hold_seconds, stop_requested=stop_requested)
        if result["final_open_positions"] or result["final_pending_orders"] or result["final_own_stops"]:
            raise DemoError("Demo cleanup incomplete; pending budget retained")
        verification = reconcile(client, result)
        result['reconciliation'] = verification
        result['settlement_status'] = 'PENDING_VERIFICATION'
        temporary = journal.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, indent=2) + '\n')
        temporary.replace(journal)
        verify_settlement(budget.snapshot(), verification, datetime.now(timezone.utc))
        settled = budget.settle(verification['usdt_equity'], expected_journal=journal,
                                verification={'journal': str(journal), 'reconciliation': verification})
        result["strategy_budget"] = settled
        result['settlement_status'] = 'VERIFIED'
        temporary.write_text(json.dumps(result, indent=2) + '\n')
        temporary.replace(journal)
        return {"status": result["status"], 'settlement_status': 'VERIFIED', "purpose": "BOUNDED_AI_EXECUTION_TEST", "journal": str(journal), "budget": settled}
    except NoTrade as exc:
        # Rejection guarantees no order request was sent.
        settled = budget.settle(before, expected_journal=journal)
        return {'status': 'WAIT', 'reason': str(exc), 'orders_sent': 0,
                'journal': str(journal), 'budget': settled}
    except Exception:
        # Pending stays latched across crashes; never start a replacement entry.
        if budget.snapshot()['pending'] is None:
            raise DemoError('Settlement committed; journal update failed, inspect budget ledger and ' + str(journal)) from None
        raise DemoError("Attempt needs reconciliation; budget locked, inspect " + str(journal)) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--recover", action="store_true", help="Read-only reconciliation; never submits or closes orders")
    parser.add_argument('--hold-seconds', type=int, default=0, choices=range(301), metavar='0..300', help='Bounded position supervision; 0 keeps the immediate execution test')
    args = parser.parse_args()
    if args.recover and args.hold_seconds:
        parser.error('--hold-seconds requires --once')
    with (ROOT / ".demo-agent.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another demo agent process is running")
        try:
            report = recover(Budget(ROOT / 'demo-strategy.sqlite3')) if args.recover else once(args.hold_seconds)
        except (DemoError, ValueError) as exc:
            report = {"status": "BLOCKED", "reason": str(exc)}
        except Exception as exc:
            report = {"status": "BLOCKED", "reason": type(exc).__name__}
        (ROOT / "demo-agent-output.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return int(report["status"] not in ("WAIT", "COOLDOWN", "COMPLETE", "DAILY_HALT", "RECOVERED", "NO_PENDING_ATTEMPT"))


if __name__ == "__main__":
    raise SystemExit(main())
