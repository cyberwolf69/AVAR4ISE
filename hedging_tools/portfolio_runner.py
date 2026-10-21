"""Foreground portfolio supervisor. Native exits remain active when the process stops."""
import argparse
import fcntl
import json
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from hedging_tools.demo_agent import Budget
from hedging_tools.okx_demo import OKXDemo, DemoError
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paths import PROJECT_ROOT, RUNTIME_DIR
from hedging_tools.portfolio import Portfolio, Store
from hedging_tools.history_worker import HistoryWorker
from hedging_tools.controls import Controls
from hedging_tools.remote_worker import RemoteWorker
from hedging_tools.engine_events import EngineEvents


def research_process(output):
    return subprocess.Popen([sys.executable, '-B', '-m', 'hedging_tools.research', '--ai', '--portfolio',
                             '--db', str(RUNTIME_DIR / 'scans.sqlite3'), '--output', str(output)],
                            cwd=PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def loop(manager, stop, interval=60, cycles=0, poll=5, launch=research_process,
         publish=lambda result: None, monotonic=time.monotonic, background=lambda: None):
    if interval < 60 or interval > 3600 or poll < 1 or poll > 30 or cycles < 0:
        raise ValueError('Use interval 60..3600, poll 1..30, cycles >= 0 (0 means continuous)')
    child, output = None, None
    completed, next_scan, started = 0, 0, 0
    last_report = None
    try:
        while not stop.is_set():
            background()
            try:
                report = manager.manage()
            except Exception as exc:
                report = {'status': 'ENTRY_BLOCKED', 'reason': str(exc) if isinstance(exc, DemoError) else type(exc).__name__}
            if report != last_report:
                publish(report)
                last_report = report
            if child is not None:
                code = child.poll()
                if code is None and monotonic() - started >= 300:
                    child.terminate()
                    try:
                        child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                    code = -1
                if code is not None:
                    completed += 1
                    data = None
                    if code == 0 and output.exists():
                        try:
                            data = json.loads(output.read_text())
                            if (not isinstance(data.get('packets'), list) or not isinstance(data.get('analyses'), list)
                                    or len(data['packets']) != len(data['analyses'])):
                                data = None
                        except (OSError, ValueError, AttributeError):
                            data = None
                    if data is not None:
                        results = []
                        for packet, analysis in zip(data['packets'], data['analyses']):
                            if stop.is_set():
                                break
                            try:
                                result = manager.enter(packet, analysis)
                            except Exception as exc:
                                result = {'status': 'ENTRY_BLOCKED', 'reason': str(exc) if isinstance(exc, (DemoError, ValueError)) else type(exc).__name__}
                            results.append({'pair': packet['input']['pair'], **result})
                        publish({'status': 'SCAN_COMPLETE', 'cycle': completed, 'decisions': results, 'alerts': data.get('alerts'),
                                 'ai': {'provider_health': data.get('provider_health'), 'errors': [{'pair': p['input']['pair'], 'reason': a.get('reason','Unknown AI error')}
                                                  for p,a in zip(data['packets'],data['analyses']) if a.get('status')=='ERROR'],
                                        'validated': sum(a.get('status')=='VALIDATED' for a in data['analyses'])}})
                    else:
                        publish({'status': 'RESEARCH_FAILED', 'cycle': completed, 'details': 'See research-output.json; position supervision continues'})
                    if output.exists():
                        output.replace(RUNTIME_DIR / 'research-output.json')
                    child = None
                    delay = interval
                    if data and data.get('retry_at', 0) > 0 and data.get('provider_health', {}).get('reason') != 'HEALTHY':
                        delay = min(interval, max(poll, data['retry_at'] - time.time()))
                    next_scan = monotonic() + delay
                    if cycles and completed >= cycles:
                        break
            if child is None and monotonic() >= next_scan and not stop.is_set():
                output = RUNTIME_DIR / ('research-' + uuid.uuid4().hex + '.json')
                try:
                    child = launch(output)
                    started = monotonic()
                    publish({'status': 'SCANNING', 'cycle': completed + 1})
                except OSError:
                    publish({'status': 'RESEARCH_FAILED', 'reason': 'Unable to start research; supervision continues'})
                    next_scan = monotonic() + interval
            stop.wait(poll)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    result = {'status': 'STOPPED' if stop.is_set() else 'CYCLE_LIMIT_REACHED', 'completed_cycles': completed,
              'positions': 'Native exits are not canceled; restart run for supervision. Inspect any ENTRY_BLOCKED errors.'}
    publish(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=60, help='Seconds between completed research scans')
    parser.add_argument('--poll', type=int, default=5, help='Seconds between portfolio checks (1..30)')
    parser.add_argument('--cycles', type=int, default=0, help='0: run until Ctrl+C; otherwise stop after this many scans')
    args = parser.parse_args()
    if not 60 <= args.interval <= 3600 or not 1 <= args.poll <= 30 or args.cycles < 0:
        parser.error('interval 60..3600, poll 1..30, cycles >= 0')
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    events = EngineEvents()
    def notify(report):
        try:
            events.report(report)
        except Exception:
            print(json.dumps({'status':'OWNER_EVENT_LOG_ERROR'}), flush=True)
    def publish(report):
        value = {'mode': 'OKX_DEMO', 'time': datetime.now(timezone.utc).isoformat(), **report}
        path = RUNTIME_DIR / 'portfolio-output.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        temporary.replace(path)
        print(json.dumps(value, ensure_ascii=False), flush=True)
        notify(value)
    with (RUNTIME_DIR / '.demo-agent.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another demo agent or runner process is running')
        old = Budget(RUNTIME_DIR / 'demo-strategy.sqlite3').snapshot()
        if old.get('pending'):
            raise SystemExit('Reconcile legacy pending attempt first: python3 main.py agent --recover')
        controls = Controls()
        with controls.locked():
            controls.initialize()
        manager = Portfolio(Store(RUNTIME_DIR / 'portfolio.sqlite3', old), OKXDemo(), OKXPublic(), controls=controls)
        worker = HistoryWorker()
        reviewer = HistoryWorker(module='hedging_tools.improve', arguments=('--evaluate',))
        shadow = HistoryWorker(module='hedging_tools.shadow', arguments=('--update',), interval=5)
        remote = RemoteWorker(publish=lambda report: (print(json.dumps(report),flush=True), notify(report)))
        notify({'status':'ENGINE_STARTED','mode':'OKX_DEMO'})
        def background():
            heartbeat = {'time': datetime.now(timezone.utc).isoformat(), 'status': 'RUNNING'}
            temporary = RUNTIME_DIR / 'engine-heartbeat.tmp'
            temporary.write_text(json.dumps(heartbeat))
            temporary.replace(RUNTIME_DIR / 'engine-heartbeat.json')
            worker.tick()
            reviewer.tick()
            shadow.tick()
            remote.tick()
            try:
                events.positions(manager.store.read())
            except Exception:
                print(json.dumps({'status':'OWNER_POSITION_EVENT_ERROR'}),flush=True)
        try:
            loop(manager, stop, args.interval, args.cycles, args.poll, publish=publish, background=background)
        finally:
            notify({'status':'ENGINE_STOPPED','positions':'Confirmed native exits remain; stop adjustments require run.'})
            remote.close()
            (RUNTIME_DIR / "engine-heartbeat.json").write_text(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "status": "STOPPED"}))
            worker.close()
            reviewer.close()
            shadow.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
