"""Reproducible synthetic paper cycle in an isolated temporary ledger."""
from hedging_tools.paths import CONFIG_PATH
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hedging_tools.demo import fixture
from hedging_tools.paper import PaperLedger


def main():
    config = json.loads(CONFIG_PATH.read_text())
    now = datetime(2026, 10, 18, 0, tzinfo=timezone.utc)
    summary = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "paper.sqlite3"
        for i, (kind, price) in enumerate((("signal", "100000"), ("funding", "100000"), ("close", "101000"))):
            at = now + timedelta(seconds=i)
            signal, market, _ = fixture(at)
            market.update(bid=price, ask=price, mark=price)
            event = {"id": f"synthetic-{i}", "type": kind, "at": at.isoformat(), "market": market, "signal": signal}
            if kind == "funding":
                event.update(rate="0.0001", settlement_at=at.isoformat())
            # Reopen every step to exercise persisted state across restarts.
            result = PaperLedger(path, config).process(event)
            summary.append({"action": result["action"], "equity": result["state"]["equity"],
                            "fees_paid": result["state"]["fees_paid"], "funding_net": result["state"]["funding_net"]})
    print(json.dumps({"source": "SYNTHETIC_TEST_NOT_PERFORMANCE", "steps": summary}, indent=2))


if __name__ == "__main__":
    main()
