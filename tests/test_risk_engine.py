import unittest
from datetime import datetime, timedelta, timezone
from hedging_tools.demo import fixture
from hedging_tools.risk_engine import load_engine


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.s, self.m, self.a = fixture(self.now)
        self.engine = load_engine()

    def decision(self):
        return self.engine.evaluate(self.s, self.m, self.a, self.now)

    def test_baseline(self):
        result = self.decision()
        self.assertEqual(result.action, "APPROVE_PAPER")
        self.assertEqual(result.leverage, 10)
        self.assertEqual(float(result.margin), 5)
        self.assertAlmostEqual(float(result.planned_risk), .35)

    def test_daily_limit_and_latch(self):
        self.a["equity"] = "45"
        self.assertEqual(self.decision().action, "HALT")
        self.a.update(equity="50", halted_today=True)
        self.assertEqual(self.decision().action, "HALT")

    def test_remaining_daily_budget(self):
        self.a["equity"] = "45.20"
        self.assertEqual(self.decision().action, "WAIT")

    def test_risk_too_large(self):
        self.s["stop"] = "97000"
        self.assertEqual(self.decision().action, "WAIT")

    def test_positions_and_pending_entries(self):
        self.a["open_positions"] = 1
        self.assertEqual(self.decision().action, "WAIT")
        self.a.update(open_positions=0, pending_entry=True)
        self.assertEqual(self.decision().action, "WAIT")

    def test_stale_and_expired(self):
        self.m["observed_at"] = (self.now - timedelta(seconds=31)).isoformat()
        self.assertEqual(self.decision().action, "WAIT")
        self.m["observed_at"] = self.now.isoformat()
        self.s["expires_at"] = self.now.isoformat()
        self.assertEqual(self.decision().action, "WAIT")

    def test_minimum_order(self):
        self.m["min_contracts"] = "1"
        self.assertEqual(self.decision().action, "WAIT")

    def test_liquidation_buffer(self):
        self.m["estimated_liquidation_by_leverage"] = {str(x): "99400" for x in range(10, 31)}
        self.assertEqual(self.decision().action, "WAIT")

    def test_short(self):
        self.s.update(action="SHORT", stop="100500")
        self.m["estimated_liquidation_by_leverage"] = {str(x): "102000" for x in range(10, 31)}
        self.assertEqual(self.decision().action, "APPROVE_PAPER")

    def test_invalid_data(self):
        for value in ("NaN", "Infinity", "bad", "-1"):
            with self.subTest(value=value):
                self.m["ask"] = value
                self.assertEqual(self.decision().action, "WAIT")


if __name__ == "__main__":
    unittest.main()
