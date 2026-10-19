"""Synthetic example only; no market data or trading connection."""
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hedging_tools.risk_engine import load_engine


def fixture(now):
    return (
        {"action": "LONG", "pair": "BTC-USDT-SWAP", "stop": "99500",
         "expires_at": (now + timedelta(minutes=15)).isoformat()},
        {"pair": "BTC-USDT-SWAP", "observed_at": now.isoformat(),
         "bid": "99999", "ask": "100000", "contract_type": "linear",
         "settle_currency": "USDT", "state": "live", "base_units_per_contract": "0.01",
         "lot_size": "0.01", "min_contracts": "0.01", "max_leverage": 30,
         "estimated_liquidation_by_leverage": {str(x): "98000" for x in range(10, 31)}},
        {"day_start_equity": "50", "equity": "50", "available_balance": "50",
         "halted_today": False, "open_positions": 0, "pending_entry": False},
    )


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    print(json.dumps({"source": "SYNTHETIC_FIXTURE", "decision": asdict(
        load_engine().evaluate(*fixture(now), now))}, indent=2))
