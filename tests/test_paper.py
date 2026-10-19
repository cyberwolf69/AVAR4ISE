from hedging_tools.paths import CONFIG_PATH
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hedging_tools.demo import fixture
from hedging_tools.paper import PaperLedger
from hedging_tools.risk_engine import number

CONFIG = json.loads(CONFIG_PATH.read_text())
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def event(identity="open", at=NOW, kind="signal", price="100000"):
    signal, market, _ = fixture(at)
    market.update(bid=price, ask=price, mark=price)
    return {"id": identity, "at": at.isoformat(), "type": kind, "market": market, "signal": signal}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.ledger = PaperLedger(self.path, CONFIG)

    def test_open_restart_and_idempotent_replay(self):
        opened = self.ledger.process(event())
        self.assertEqual(opened["action"], "OPEN_PAPER")
        self.assertLess(number(opened["state"]["cash"]), 50)
        restarted = PaperLedger(self.path, CONFIG)
        self.assertEqual(restarted.snapshot(), opened["state"])
        self.assertEqual(restarted.process(event()), opened)

    def test_close_pnl_and_fees(self):
        opened = self.ledger.process(event())
        position = opened["state"]["position"]
        closed = self.ledger.process(event("exit", NOW + timedelta(seconds=1), "close", "101000"))
        units, entry = number(position["units"]), number(position["entry"])
        fill = number("101000") * number("0.9998")
        gross = units * (fill - entry)
        fee = units * (entry + fill) * number("0.0005")
        self.assertEqual(number(closed["state"]["cash"]), 50 + gross - fee)
        self.assertIsNone(closed["state"]["position"])

    def test_gap_stop_fills_at_quote_not_stop(self):
        self.ledger.process(event())
        closed = self.ledger.process(event("gap", NOW + timedelta(seconds=1), "mark", "98000"))
        self.assertEqual(closed["reason"], "STOP_LOSS")
        self.assertLess(number(closed["fill"]), 98000)

    def test_daily_halt_survives_restart_and_price_recovery(self):
        self.ledger.process(event())
        closed = self.ledger.process(event("crash", NOW + timedelta(seconds=1), "mark", "80000"))
        self.assertEqual(closed["reason"], "DAILY_LOSS_LIMIT")
        self.assertTrue(closed["state"]["halted_today"])
        restarted = PaperLedger(self.path, CONFIG)
        result = restarted.process(event("retry", NOW + timedelta(seconds=2)))
        self.assertEqual(result["action"], "HALT")

    def test_funding_and_duplicate_settlement(self):
        self.ledger.process(event())
        before = self.ledger.snapshot()
        funding = event("funding", NOW + timedelta(seconds=1), "funding")
        funding.update(rate="0.001", settlement_at=funding["at"])
        after = self.ledger.process(funding)["state"]
        payment = number(before["position"]["units"]) * 100000 * number("0.001")
        self.assertEqual(number(after["cash"]), number(before["cash"]) - payment)
        duplicate = deepcopy(funding)
        duplicate["id"] = "different-id"
        with self.assertRaises(ValueError):
            self.ledger.process(duplicate)
        self.assertEqual(self.ledger.snapshot(), after)

    def test_short_receives_positive_funding(self):
        opening = event()
        opening["signal"].update(action="SHORT", stop="100500")
        opening["market"]["estimated_liquidation_by_leverage"] = {str(x): "102000" for x in range(10, 31)}
        self.ledger.process(opening)
        before = self.ledger.snapshot()
        funding = event("funding", NOW + timedelta(seconds=1), "funding")
        funding.update(rate="0.001", settlement_at=funding["at"])
        after = self.ledger.process(funding)["state"]
        self.assertGreater(number(after["cash"]), number(before["cash"]))

    def test_one_position(self):
        self.ledger.process(event())
        result = self.ledger.process(event("second", NOW + timedelta(seconds=1)))
        self.assertEqual(result["action"], "WAIT")

    def test_stale_quote_rolls_back(self):
        bad = event()
        bad["market"]["observed_at"] = (NOW - timedelta(seconds=31)).isoformat()
        before = self.ledger.snapshot()
        with self.assertRaises(ValueError):
            self.ledger.process(bad)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_changed_event_id_rejected(self):
        self.ledger.process(event())
        with self.assertRaises(ValueError):
            self.ledger.process(event(price="100001"))

    def test_time_cannot_go_back(self):
        self.ledger.process(event())
        with self.assertRaises(ValueError):
            self.ledger.process(event("past", NOW - timedelta(seconds=1), "mark"))

    def test_midnight_required_and_day_equity_reset(self):
        self.ledger.process(event())
        midnight = datetime(2026, 10, 17, 17, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            self.ledger.process(event("late", midnight + timedelta(seconds=1), "mark"))
        result = self.ledger.process(event("midnight", midnight, "mark"))
        self.assertEqual(result["state"]["day"], "2026-10-18")
        self.assertEqual(result["state"]["day_start_equity"], result["state"]["equity"])

    def test_risk_rejection_creates_no_position(self):
        bad = event()
        bad["signal"]["stop"] = "90000"
        self.assertEqual(self.ledger.process(bad)["action"], "WAIT")
        self.assertIsNone(self.ledger.snapshot()["position"])
