from hedging_tools.paths import CONFIG_PATH
import io
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from hedging_tools.okx_public import OKXPublic, MarketDataError
from hedging_tools.scan import run
from hedging_tools.scanner import screen


NOW = datetime(2026, 10, 17, 12, 0, 10, tzinfo=timezone.utc)
CONFIG = {**json.loads(CONFIG_PATH.read_text()), "setup_model": "ema_baseline"}


def fixture(pair="BTC-USDT-SWAP"):
    now_ms = int(NOW.timestamp() * 1000)
    def candles(seconds):
        end = now_ms // (seconds * 1000) * seconds * 1000
        rows = []
        for i in range(100):
            close = 99000 + 10 * i
            rows.append([str(end - (100 - i) * seconds * 1000), str(close - 5),
                         str(close + 10), str(close - 10), str(close), "10", "1",
                         "2000" if i == 99 else "1000", "1"])
        return list(reversed(rows))
    return {"pair": pair,
            "instrument": {"instId": pair, "ctType": "linear", "settleCcy": "USDT",
                           "state": "live", "ctValCcy": pair.split("-")[0], "ctVal": "0.01",
                           "ctMult": "1", "lotSz": "0.01", "minSz": "0.01", "lever": "100"},
            "ticker": {"instId": pair, "bidPx": "99990", "askPx": "99991", "ts": str(now_ms)},
            "funding": {"instId": pair, "fundingRate": "0.0001", "ts": str(now_ms),
                        "fundingTime": str(now_ms + 3600000)},
            "interest": {"instId": pair, "oi": "10000", "ts": str(now_ms)},
            "candles_15m": candles(900), "candles_1h": candles(3600)}


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def result(self):
        return screen(self.data, CONFIG, NOW)

    def test_candidate_is_not_order_approval(self):
        result = self.result()
        self.assertEqual(result["status"], "WATCHLIST")
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["candidate"]["action"], "LONG")
        self.assertTrue(all(10 <= x["leverage"] <= 30 for x in result["candidate"]["sizing_options"]))

    def test_unclosed_candle_excluded(self):
        baseline = self.result()
        current = self.data["candles_15m"][0].copy()
        current[8] = "0"
        current[4] = "99999999"
        self.data["candles_15m"].insert(0, current)
        self.assertEqual(self.result(), baseline)

    def test_gap_and_duplicate(self):
        self.data["candles_15m"].pop(10)
        self.assertEqual(self.result()["status"], "DATA_ERROR")
        self.data = fixture()
        self.data["candles_15m"].append(self.data["candles_15m"][0])
        self.assertEqual(self.result()["status"], "DATA_ERROR")

    def test_stale_quote(self):
        self.data["ticker"]["ts"] = str(int(NOW.timestamp() * 1000) - 31000)
        self.assertEqual(self.result()["status"], "DATA_ERROR")

    def test_spread_blocks_candidate(self):
        self.data["ticker"]["askPx"] = "100200"
        self.assertEqual(self.result()["status"], "WAIT")

    def test_insufficient_size(self):
        self.data["instrument"]["minSz"] = "100"
        self.assertEqual(self.result()["status"], "WAIT")

    def test_nonfinite_and_wrong_pair(self):
        self.data["ticker"]["askPx"] = "NaN"
        self.assertEqual(self.result()["status"], "DATA_ERROR")
        self.data = fixture()
        self.data["interest"]["instId"] = "OTHER"
        self.assertEqual(self.result()["status"], "DATA_ERROR")

    def test_one_pair_failure_does_not_drop_other_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            client = Mock()
            client.snapshot.side_effect = [MarketDataError("Unavailable"), fixture("ETH-USDT-SWAP")]
            output = run(client, CONFIG, database, now=NOW)
            self.assertEqual([r["status"] for r in output["results"]], ["DATA_ERROR", "WATCHLIST"])
            with closing(sqlite3.connect(database)) as db, db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM scans").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(DISTINCT run_id) FROM scans").fetchone()[0], 1)


class ClientTests(unittest.TestCase):
    def test_retry_api_error(self):
        opener = Mock(side_effect=[io.StringIO('{"code":"50011"}'), io.StringIO('{"code":"0","data":[]}')])
        sleep = Mock()
        self.assertEqual(OKXPublic(opener, sleep).get("ticker", instId="BTC-USDT-SWAP"), [])
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(opener.call_args.args[0].get_method(), "GET")
        sleep.assert_called_once_with(.5)

    def test_bounded_retry(self):
        opener = Mock(side_effect=TimeoutError("timeout"))
        with self.assertRaises(MarketDataError):
            OKXPublic(opener, Mock()).get("ticker")
        self.assertEqual(opener.call_count, 3)

    def test_trade_endpoint_not_available(self):
        opener = Mock()
        with self.assertRaises(KeyError):
            OKXPublic(opener).get("trade")
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
