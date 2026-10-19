"""Run one read-only scan and persist all successes, rejections, and errors."""
from hedging_tools.paths import CONFIG_PATH
import argparse
import json
import sqlite3
from contextlib import closing
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.okx_public import OKXPublic, MarketDataError
from hedging_tools.scanner import screen

from hedging_tools.paths import RUNTIME_DIR as ROOT


def record(database, run_id, source, raw, result):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, run_id TEXT, recorded_at TEXT, source TEXT, pair TEXT, raw_json TEXT, result_json TEXT)")
        db.execute("INSERT INTO scans (run_id, recorded_at, source, pair, raw_json, result_json) VALUES (?, ?, ?, ?, ?, ?)",
                   (run_id, datetime.now(timezone.utc).isoformat(), source, result["pair"],
                    json.dumps(raw), json.dumps(result)))


def run(client, config, database, snapshots=None, now=None, workers=1):
    run_id = str(uuid.uuid4())
    def inspect(pair):
        raw = None
        try:
            raw = snapshots[pair] if snapshots is not None else client.snapshot(pair)
            result = screen(raw, config, now)
        except (MarketDataError, KeyError, ValueError, TypeError) as exc:
            result = {"pair": pair, "status": "DATA_ERROR", "execution": "DISABLED", "reasons": [str(exc)]}
        return raw, result
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        for raw, result in pool.map(inspect, config['pairs']):
            record(database, run_id, "fixture" if snapshots is not None else "okx_public", raw, result)
            results.append(result)
    return {"run_id": run_id, "mode": "READ_ONLY_SCREENING", "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="Offline JSON with as_of and snapshots")
    parser.add_argument("--db", type=Path, default=ROOT / "scans.sqlite3")
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text())
    snapshots = now = None
    if args.fixture:
        data = json.loads(args.fixture.read_text())
        snapshots, now = data["snapshots"], datetime.fromisoformat(data["as_of"])
    output = run(OKXPublic(), config, args.db, snapshots, now)
    print(json.dumps(output, indent=2))
    return 1 if any(row["status"] == "DATA_ERROR" for row in output["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
