"""Signed OKX demo client; the CLI runs read-only preflight."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler
from hedging_tools.ai_client import NoRedirect
from hedging_tools.okx_public import OKXPublic

from hedging_tools.paths import PROJECT_ROOT as ROOT
KEYS = ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET_KEY", "OKX_DEMO_PASSPHRASE")


class DemoError(Exception):
    def __init__(self, message, api_code=None):
        super().__init__(message)
        self.api_code = api_code


def credentials(path=ROOT / ".env"):
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in KEYS:
            values[key.strip()] = value.strip().strip('\"\'')
    missing = [k for k in KEYS if not values.get(k)]
    if missing:
        raise DemoError("Fill local .env fields: " + ", ".join(missing))
    return values


class OKXDemo:
    ROUTES = {"balance": "/api/v5/account/balance", "positions": "/api/v5/account/positions",
              "config": "/api/v5/account/config", "orders": "/api/v5/trade/orders-pending",
              "instruments": "/api/v5/account/instruments", "bills": "/api/v5/account/bills"}
    READ_TRADE = {"order": "/api/v5/trade/order", "algos": "/api/v5/trade/orders-algo-pending",
                  "algo": "/api/v5/trade/order-algo"}
    WRITE_TRADE = {"leverage": "/api/v5/account/set-leverage", "order": "/api/v5/trade/order",
                   "amend_algo": "/api/v5/trade/amend-algos",
                   "cancel": "/api/v5/trade/cancel-order", "cancel_algos": "/api/v5/trade/cancel-algos"}

    def __init__(self, keys=None, opener=None, clock=None):
        self.keys = keys if keys is not None else credentials()
        self.opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=OKXPublic().context)).open
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, endpoint, **params):
        path = {**self.ROUTES, **self.READ_TRADE}[endpoint]
        if params:
            path += "?" + urlencode(params)
        return self._request("GET", path)

    def post(self, endpoint, body):
        return self._request("POST", self.WRITE_TRADE[endpoint], json.dumps(body, separators=(",", ":")))

    def _request(self, method, path, body=""):
        stamp = self.clock().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(hmac.new(self.keys[KEYS[1]].encode(), (stamp + method + path + body).encode(), hashlib.sha256).digest()).decode()
        request = Request("https://openapi.okx.com" + path, data=body.encode() if body else None, headers={
            "OK-ACCESS-KEY": self.keys[KEYS[0]], "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": stamp, "OK-ACCESS-PASSPHRASE": self.keys[KEYS[2]],
            "x-simulated-trading": "1", "Content-Type": "application/json",
            "User-Agent": "AVAR4ISE-DemoPreflight/0.1", "Accept": "application/json"}, method=method)
        try:
            with self.opener(request, timeout=15) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise DemoError("Oversized response")
            result = json.loads(raw)
            if result.get("code") != "0":
                code = str(result.get("code", ""))
                raise DemoError("OKX demo API error code " + (code if code.isdigit() else "unknown"),
                                api_code=code if code.isdigit() else None)
            if not isinstance(result.get("data"), list):
                raise DemoError("Invalid data schema")
            return result["data"]
        except HTTPError as exc:
            code = exc.code
            detail = ""
            try:
                error = json.loads(exc.read(65536))
                api_code = str(error.get("code", ""))
                if api_code.isdigit():
                    detail = "; API code " + api_code
            except (ValueError, TypeError, AttributeError, OSError):
                detail = "; no structured API code"
            exc.close()
            raise DemoError(f"OKX demo HTTP {code}{detail}") from None
        except (OSError, URLError):
            raise DemoError("OKX demo network/TLS failure") from None
        except (ValueError, TypeError, AttributeError):
            raise DemoError("Invalid OKX demo response") from None


def main():
    try:
        client = OKXDemo()
        balance = client.get("balance", ccy="USDT")
        config = client.get("config")
        positions = client.get("positions", instType="SWAP")
        orders = client.get("orders", instType="SWAP")
        if len(balance) != 1 or len(config) != 1:
            raise DemoError("Unexpected account snapshot")
        report = {"mode": "OKX_DEMO_PREFLIGHT", "demo_header": "1", "order_submission": "NOT_REQUESTED",
                  "account_mode": config[0].get("acctLv"), "position_mode": config[0].get("posMode"),
                  "usdt": [{k: d.get(k) for k in ("ccy", "eq", "availBal", "availEq")} for d in balance[0].get("details", []) if d.get("ccy") == "USDT"],
                  "swap_position_records": len(positions), "pending_swap_orders": len(orders)}
        print(json.dumps(report, indent=2))
        return 0
    except DemoError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "orders_sent": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
