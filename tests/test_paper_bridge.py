import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
from hedging_tools.analyst import make_packet
from hedging_tools.paper import PaperLedger
from hedging_tools.paper_bridge import apply_proposal, cycle
from tests.test_paper import CONFIG, NOW, event


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = PaperLedger(Path(self.temp.name) / "paper.sqlite3", CONFIG)
        self.market = event()["market"]
        self.packet = make_packet({"pair": "BTC-USDT-SWAP", "status": "WATCHLIST",
                                   "candidate": {"action": "LONG", "stop_reference": "99500"}},
                                  [{"id": "synthetic-news"}], [{"status": "OK"}], NOW)
        self.proposal = {"packet_id": self.packet["input"]["packet_id"], "pair": "BTC-USDT-SWAP",
                         "action": "LONG", "reason": "Synthetic test", "invalidation": "Test stop",
                         "source_ids": ["synthetic-news"], "expires_at": self.packet["input"]["expires_at"]}

    def test_validated_signal_opens_only_paper(self):
        result = apply_proposal(self.ledger, self.proposal, self.packet, self.market, NOW)
        self.assertEqual(result["action"], "OPEN_PAPER")
        self.assertEqual(result["execution"], "SIMULATED_ONLY")

    def test_duplicate_proposal_cannot_reopen_after_exit(self):
        apply_proposal(self.ledger, self.proposal, self.packet, self.market, NOW)
        self.ledger.process(event("exit", NOW + timedelta(seconds=1), "close", "101000"))
        market = deepcopy(self.market)
        market["observed_at"] = (NOW + timedelta(seconds=2)).isoformat()
        result = apply_proposal(self.ledger, self.proposal, self.packet, market, NOW + timedelta(seconds=2))
        self.assertEqual(result["action"], "SKIP_ALREADY_PROCESSED")
        self.assertIsNone(result["state"]["position"])

    def test_missing_liquidation_estimate_does_not_open(self):
        self.market["estimated_liquidation_by_leverage"] = {}
        result = apply_proposal(self.ledger, self.proposal, self.packet, self.market, NOW)
        self.assertEqual(result["action"], "WAIT")
        self.assertIsNone(result["state"]["position"])

    def test_expired_rejected_before_network(self):
        client = Mock()
        result = cycle(client, self.ledger, [(self.packet, {"status": "VALIDATED", "proposal": self.proposal})],
                       clock=lambda: NOW + timedelta(minutes=16))
        self.assertEqual(result[0]["action"], "REJECT")
        client.get.assert_not_called()

    def test_invented_source_rejected(self):
        self.proposal["source_ids"] = ["unknown"]
        with self.assertRaises(ValueError):
            apply_proposal(self.ledger, self.proposal, self.packet, self.market, NOW)
        self.assertIsNone(self.ledger.snapshot()["position"])

    def test_wrong_pair_rejected(self):
        self.market["pair"] = "ETH-USDT-SWAP"
        with self.assertRaises(ValueError):
            apply_proposal(self.ledger, self.proposal, self.packet, self.market, NOW)

    def test_wait_does_not_open(self):
        self.proposal["action"] = "WAIT"
        result = apply_proposal(self.ledger, self.proposal, self.packet, self.market, NOW)
        self.assertEqual(result["action"], "WAIT")
        self.assertEqual(result["state"]["cash"], "50")
