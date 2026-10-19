"""Bounded foreground OKX Demo scanner with a configurable trade limit."""
import argparse
import fcntl
import json
import signal
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from hedging_tools.demo_agent import ROOT, once, TRADE_COOLDOWN_SECONDS
from hedging_tools.okx_demo import DemoError


class RunLog:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS runner_cycles (run_id TEXT, cycle INTEGER, started_at TEXT, finished_at TEXT, report TEXT, PRIMARY KEY(run_id, cycle))')

    def start(self, run_id, cycle):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO runner_cycles VALUES (?,?,?,NULL,NULL)',
                       (run_id, cycle, datetime.now(timezone.utc).isoformat()))

    def finish(self, run_id, cycle, report):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE runner_cycles SET finished_at=?,report=? WHERE run_id=? AND cycle=?',
                       (datetime.now(timezone.utc).isoformat(), json.dumps(report), run_id, cycle))


def run_loop(cycles, interval, hold_seconds, log, stop, cycle_fn=once, publish=lambda value: None, max_trades=1):
    if not 1 <= cycles <= 20 or not 60 <= interval <= 3600 or not 0 <= hold_seconds <= 300:
        raise ValueError('Use 1..20 cycles, interval 60..3600 seconds, holding 0..300 seconds')
    if not isinstance(max_trades, int) or not 1 <= max_trades <= 3:
        raise ValueError('Trade limit must be 1..3 per session')
    run_id = uuid.uuid4().hex
    result = {'mode': 'OKX_DEMO', 'run_id': run_id, 'status': 'RUNNING',
              'requested_cycles': cycles, 'completed_cycles': 0, 'interval_seconds': interval,
              'hold_seconds': hold_seconds, 'trade_limit': max_trades, 'verified_trades': 0}
    publish(result.copy())
    for index in range(1, cycles + 1):
        if stop.is_set():
            result['status'] = 'STOPPED'
            break
        log.start(run_id, index)  # An interrupted cycle remains visible in SQLite.
        try:
            report = cycle_fn(hold_seconds, stop_requested=stop.is_set)
        except (DemoError, ValueError) as exc:
            report = {'status': 'BLOCKED', 'reason': str(exc)}
        except Exception as exc:
            report = {'status': 'BLOCKED', 'reason': type(exc).__name__}
        log.finish(run_id, index, report)
        result.update(completed_cycles=index, last_cycle=report)
        delay = interval
        if report.get('status') == 'COMPLETE' and report.get('settlement_status') == 'VERIFIED':
            result['verified_trades'] += 1
            delay = max(interval, TRADE_COOLDOWN_SECONDS)
            if report.get('budget', {}).get('halted'):
                result['status'] = 'DAILY_HALT'
            elif result['verified_trades'] >= max_trades:
                result['status'] = 'TRADE_LIMIT_REACHED'
        elif report.get('status') == 'STOPPED' and report.get('orders_sent') == 0:
            result['status'] = 'STOPPED'
        elif report.get('status') not in ('WAIT', 'COOLDOWN') or report.get('orders_sent') != 0:
            result['status'] = 'DAILY_HALT' if report.get('status') == 'DAILY_HALT' else 'BLOCKED'
        if result['status'] == 'RUNNING' and stop.is_set():
            result['status'] = 'STOPPED'
        elif result['status'] == 'RUNNING' and index == cycles:
            result['status'] = 'CYCLE_LIMIT_REACHED'
        result['next_wait_seconds'] = delay if result['status'] == 'RUNNING' else 0
        publish(result.copy())
        if result['status'] != 'RUNNING':
            break
        if stop.wait(delay):
            result['status'] = 'STOPPED'
            result['next_wait_seconds'] = 0
            break
    publish(result.copy())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=2)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--hold-seconds', type=int, default=60)
    parser.add_argument('--max-trades', type=int, default=1, help='1..3 verified trades per session; default 1')
    args = parser.parse_args()
    stop = threading.Event()
    def request_stop(signum, frame):
        # Finish the current bounded cycle, including cleanup, before exiting.
        stop.set()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    def publish(report):
        path = ROOT / 'demo-runner-output.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(path)
        print(json.dumps(report), flush=True)
    with (ROOT / '.demo-agent.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another demo agent or runner process is running')
        result = run_loop(args.cycles, args.interval, args.hold_seconds,
                          RunLog(ROOT / 'demo-runner.sqlite3'), stop, publish=publish, max_trades=args.max_trades)
    return int(result['status'] == 'BLOCKED')


if __name__ == '__main__':
    raise SystemExit(main())
