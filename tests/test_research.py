import io
import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
from hedging_tools.analyst import make_packet, validate_proposal
from hedging_tools.news import fetch_news, initialize, news_for_pair, parse_feed, store
from hedging_tools.scanner import screen
from tests.test_scanner import NOW, CONFIG, fixture

RSS = b'''<rss><channel><item><title>Bitcoin market update</title>
<link>https://example.com/bitcoin</link><pubDate>Sat, 17 Oct 2026 11:00:00 GMT</pubDate>
</item></channel></rss>'''


class NewsTests(unittest.TestCase):
    def test_first_seen_preserved_and_no_future_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            articles, _ = parse_feed(RSS, "Test", NOW)
            with closing(sqlite3.connect(path)) as db, db:
                initialize(db)
                store(db, articles)
                newer, _ = parse_feed(RSS, "Test", NOW + timedelta(hours=1))
                store(db, newer)
            self.assertEqual(news_for_pair(path, "BTC-USDT-SWAP", NOW - timedelta(seconds=1)), [])
            selected = news_for_pair(path, "BTC-USDT-SWAP", NOW)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]["first_seen_at"], NOW.isoformat())
            self.assertEqual(news_for_pair(path, "ETH-USDT-SWAP", NOW), [])

    def test_duplicate_title_across_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                initialize(db)
                store(db, parse_feed(RSS, "One", NOW)[0])
                store(db, parse_feed(RSS.replace(b"example.com", b"other.com"), "Two", NOW)[0])
            self.assertEqual(len(news_for_pair(path, "BTC-USDT-SWAP", NOW)), 1)

    def test_unsafe_xml_and_future_item(self):
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE rss><rss/>', "Test", NOW)
        articles, rejected = parse_feed(RSS.replace(b"2026", b"2027"), "Test", NOW)
        self.assertEqual((articles, rejected), ([], 1))

    def test_partial_feed_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            opener = Mock(side_effect=[OSError("unavailable"), io.BytesIO(RSS)])
            health = fetch_news(path, opener, NOW)
            self.assertEqual([h["status"] for h in health], ["ERROR", "OK"])
            self.assertEqual(len(news_for_pair(path, "BTC-USDT-SWAP", NOW)), 1)


class AnalystTests(unittest.TestCase):
    def setUp(self):
        articles = parse_feed(RSS, "Test", NOW)[0]
        self.packet = make_packet(screen(fixture(), CONFIG, NOW), articles, [{"status": "OK"}], NOW)
        self.proposal = {"packet_id": self.packet["input"]["packet_id"], "pair": "BTC-USDT-SWAP",
                         "action": "LONG", "reason": "Test only", "invalidation": "Trend reverses",
                         "source_ids": [articles[0]["id"]],
                         "expires_at": (NOW + timedelta(minutes=5)).isoformat()}

    def test_valid_proposal_still_cannot_execute(self):
        result = validate_proposal(self.proposal, self.packet, NOW)
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["signal"]["stop"], self.packet["input"]["market"]["candidate"]["stop_reference"])

    def test_invented_source(self):
        self.proposal["source_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            validate_proposal(self.proposal, self.packet, NOW)

    def test_technical_setup_without_news(self):
        self.proposal["source_ids"] = []
        self.packet["input"]["news"] = []
        self.packet["input"]["feed_health"] = [{"status": "ERROR"}]
        self.assertEqual(validate_proposal(self.proposal, self.packet, NOW)["signal"]["action"], "LONG")

    def test_cannot_override_leverage(self):
        self.proposal["leverage"] = 100
        with self.assertRaises(ValueError):
            validate_proposal(self.proposal, self.packet, NOW)

    def test_expired_proposal(self):
        with self.assertRaises(ValueError):
            validate_proposal(self.proposal, self.packet, NOW + timedelta(minutes=6))

    def test_direction_mismatch(self):
        self.proposal["action"] = "SHORT"
        with self.assertRaises(ValueError):
            validate_proposal(self.proposal, self.packet, NOW)

    def test_wait_without_news(self):
        self.proposal.update(action="WAIT", source_ids=[])
        self.assertEqual(validate_proposal(self.proposal, self.packet, NOW)["signal"]["action"], "WAIT")

    def test_ai_cannot_upgrade_wait_to_trade(self):
        self.packet["input"]["market"]["status"] = "WAIT"
        with self.assertRaises(ValueError):
            validate_proposal(self.proposal, self.packet, NOW)

    def test_review_wait_is_valid_without_trade_permission(self):
        self.packet["input"]["market"]["status"] = "WAIT"
        self.proposal["action"] = "WAIT"
        result = validate_proposal(self.proposal, self.packet, NOW)
        self.assertEqual(result["signal"]["action"], "WAIT")
        self.assertEqual(result["execution"], "DISABLED")


if __name__ == "__main__":
    unittest.main()
