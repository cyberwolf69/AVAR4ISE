"""Explicit synthetic integration check; never a trading recommendation."""
from hedging_tools.paths import RUNTIME_DIR
import json
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.ai_client import AIClient, AIError
from hedging_tools.analyst import make_packet


def main():
    client = AIClient()
    results = []
    # Test abstention and schema on synthetic snapshots, not predictive quality.
    for pair, state in (("BTC-USDT-SWAP", "WAIT"), ("ETH-USDT-SWAP", "WATCHLIST")):
        now = datetime.now(timezone.utc)
        market = {"pair": pair, "status": state, "execution": "DISABLED",
                  "source": "SYNTHETIC_INTEGRATION_TEST", "reasons": ["Synthetic test; no real market data"]}
        if state == "WATCHLIST":
            market["candidate"] = {"action": "LONG", "entry": "2000", "stop_reference": "1990"}
        envelope = make_packet(market, [], [{"status": "ERROR", "source": "synthetic"}], now)
        row = {"pair": pair, "source": "SYNTHETIC_INTEGRATION_TEST"}
        try:
            row.update(status="VALIDATED", **client.analyze(envelope))
        except AIError as exc:
            row.update(status="ERROR", reason=str(exc))
        results.append(row)
    output = {"scope": "SYNTHETIC_SCHEMA_AND_ABSTENTION_CHECK_ONLY", "execution": "DISABLED", "results": results}
    (RUNTIME_DIR / "ai-evaluation.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    return int(any(r["status"] == "ERROR" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
