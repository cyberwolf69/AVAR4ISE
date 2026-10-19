"""Read public closed candles and save isolated mean-reversion research."""
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from hedging_tools.okx_public import OKXPublic, MarketDataError
from hedging_tools.scanner import closed_candles
from hedging_tools.mean_reversion import analyze, forward_diagnostics

from hedging_tools.paths import RUNTIME_DIR as ROOT


def run(client, database, now=None):
    now = now or datetime.now(timezone.utc)
    report = {'run_id': uuid.uuid4().hex, 'observed_at': now.isoformat(),
              'mode': 'MEAN_REVERSION_RESEARCH_ONLY', 'execution': 'DISABLED',
              'timeframe': '15m', 'results': []}
    for pair in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP'):
        raw = None
        try:
            raw = client.get('candles', instId=pair, bar='15m', limit='300')
            candles = closed_candles(raw, 900, int(now.timestamp() * 1000))
            prices = [float(c[3]) for c in candles]
            result = {'pair': pair, 'closed_bars': len(prices),
                      'last_candle_open_ms': candles[-1][0],
                      'analysis': analyze(prices), 'forward_diagnostics': forward_diagnostics(prices)}
        except (MarketDataError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
            result = {'pair': pair, 'status': 'DATA_ERROR', 'reason': str(exc), 'execution': 'DISABLED'}
        report['results'].append(result)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS reversion_runs (run_id TEXT, pair TEXT, observed_at TEXT, raw_json TEXT, result_json TEXT, PRIMARY KEY(run_id,pair))')
            db.execute('INSERT INTO reversion_runs VALUES (?,?,?,?,?)',
                       (report['run_id'], pair, now.isoformat(), json.dumps(raw), json.dumps(result)))
    return report


def main():
    report = run(OKXPublic(), ROOT / 'reversion-research.sqlite3')
    path = ROOT / 'reversion-research-output.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    return int(any(r.get('status') == 'DATA_ERROR' for r in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
