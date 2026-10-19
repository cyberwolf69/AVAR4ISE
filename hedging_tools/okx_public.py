"""Read-only OKX market client: no credentials and no trading endpoints."""
import json
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MarketDataError(Exception):
    pass


class OKXPublic:
    ENDPOINTS = {
        "instruments": "/api/v5/public/instruments",
        "ticker": "/api/v5/market/ticker",
        "tickers": "/api/v5/market/tickers",
        "mark_history": "/api/v5/market/history-mark-price-candles",
        "candles": "/api/v5/market/candles",
        "funding": "/api/v5/public/funding-rate",
        "interest": "/api/v5/public/open-interest",
        "mark": "/api/v5/public/mark-price",
    }

    def __init__(self, opener=urlopen, sleeper=time.sleep):
        self.opener, self.sleeper = opener, sleeper
        self.context = ssl.create_default_context()
        # Some macOS Python installers omit the default CA bundle. Keep TLS
        # verification enabled and load the OS PEM bundle in that case.
        if ssl.get_default_verify_paths().cafile is None and Path("/etc/ssl/cert.pem").is_file():
            self.context.load_verify_locations("/etc/ssl/cert.pem")

    def get(self, endpoint, **params):
        path = self.ENDPOINTS[endpoint]
        request = Request("https://www.okx.com" + path + "?" + urlencode(params),
                          headers={"User-Agent": "AVAR4ISE-PublicScanner/0.1"})
        for attempt in range(3):
            try:
                with self.opener(request, timeout=10, context=self.context) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise MarketDataError("Malformed response")
                if payload.get("code") in ("50011", "50040"):
                    raise MarketDataError("Rate limited")
                if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
                    raise MarketDataError(f"OKX response error: {payload.get('code')}")
                return payload["data"]
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, MarketDataError) as exc:
                if attempt == 2:
                    raise MarketDataError(f"{endpoint}: {exc}") from exc
                self.sleeper(0.5 * 2 ** attempt)

    def snapshot(self, pair):
        def one(endpoint, **kwargs):
            rows = self.get(endpoint, instId=pair, **kwargs)
            if len(rows) != 1 or rows[0].get("instId") != pair:
                raise MarketDataError(f"Missing or mismatched {endpoint} for {pair}")
            return rows[0]
        result = {
            "pair": pair,
            "instrument": one("instruments", instType="SWAP"),
            "candles_15m": self.get("candles", instId=pair, bar="15m", limit="100"),
            "candles_1h": self.get("candles", instId=pair, bar="1H", limit="100"),
            "funding": one("funding"),
            "interest": one("interest", instType="SWAP"),
        }
        # Fetch the quote last so analysis receives the freshest available ticker.
        result["ticker"] = one("ticker")
        return result
