"""Loopback-only, read-only monitoring. No trading or provider calls."""
import argparse
import fcntl
import json
import math
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from hedging_tools.paths import RUNTIME_DIR, PACKAGE_DIR


def age(stamp, now):
    try:
        value = now-datetime.fromisoformat(stamp).timestamp()
        return round(value, 1) if value >= 0 else None
    except (ValueError, TypeError):
        return None


def select(value, keys):
    return {k:value.get(k) for k in keys}


def snapshot(root=RUNTIME_DIR, now=None):
    now = time.time() if now is None else now
    warnings = []
    def file(name):
        try:
            path = root/name
            if not path.exists(): return {}
            if path.stat().st_size > 8_000_000: raise ValueError()
            value = json.loads(path.read_text())
            return value if isinstance(value,dict) else {}
        except (OSError, ValueError):
            warnings.append(name+' unavailable')
            return {}
    def query(name, sql):
        path = root/name
        if not path.exists(): return []
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
                return db.execute(sql).fetchall()
        except sqlite3.Error:
            warnings.append(name+' unavailable')
            return []
    def stored(name, sql):
        rows = query(name, sql)
        try: return json.loads(rows[0][0]) if rows else {}
        except (ValueError, TypeError):
            warnings.append(name+' invalid data'); return {}
    beat = file('engine-heartbeat.json')
    report = file('portfolio-output.json')
    research = file('research-output.json')
    ai_activity = file('ai-activity.json')
    heartbeat_age = age(beat.get('time'), now)
    locked = False
    try:
        with (root/'.demo-agent.lock').open('r') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: locked=True
    except OSError: pass
    engine = 'RUNNING' if locked and beat.get('status')=='RUNNING' and heartbeat_age is not None and heartbeat_age<=30 else 'STALE' if locked else 'OFFLINE'
    portfolio = stored('portfolio.sqlite3','SELECT data FROM portfolio WHERE id=1')
    controls = stored('controls.sqlite3','SELECT data FROM settings WHERE id=1')
    positions = [select(p, ('id','pair','direction','status','entry','limit_price','current_stop','stop','stage','remaining','pnl','upl','leverage','exit_kind')) for p in portfolio.get('positions',[])][-100:]
    active = [p for p in positions if p['status']!='CLOSED']
    packets = research.get('packets',[])
    analyses = {a.get('packet_id'):a for a in research.get('analyses',[])}
    scans=[]
    for envelope in packets[:20]:
        data=envelope.get('input',{});market=data.get('market',{});review=analyses.get(data.get('packet_id'),{})
        scans.append(dict(pair=data.get('pair'),status=market.get('status'),reasons=market.get('reasons',[])[:3],
                          review=review.get('status'),action=review.get('proposal',{}).get('action'),
                          publication=review.get('publication_basis'),observed_at=market.get('observed_at')))
    scan_stamp = max((p.get('input',{}).get('created_at','') for p in packets), default='')
    scan_age = age(scan_stamp,now)
    provider = select(research.get('provider_health',{}), ('reason','retry_at','failures'))
    retry = provider.get('retry_at') or 0
    provider['remaining_seconds'] = max(0,math.ceil(retry-now))
    provider['state'] = 'COOLDOWN' if retry>now else 'HEALTHY' if provider['reason']=='HEALTHY' else 'NOT_CHECKED' if provider['reason'] in (None,'NOT_CALLED') else 'RETRY_READY'
    signals=[]
    for row in query('signal-history.sqlite3','SELECT data FROM tracked_signals ORDER BY next_ms DESC LIMIT 30'):
        try:
            s=json.loads(row[0]); signals.append(select(s,('id','pair','direction','status','stage','entry','current_stop','targets','quality','publication_basis','issued_ms')))
        except (ValueError, TypeError): warnings.append('Invalid reference record')
    deliveries=query('alerts.sqlite3','SELECT channel,status,created_at FROM alert_deliveries ORDER BY created_at DESC LIMIT 12')
    activity=[dict(channel=r[0],status=r[1],time=r[2]) for r in deliveries]
    tokens=0
    for row in query('scans.sqlite3',f'SELECT usage FROM provider_usage WHERE at>={now-86400} ORDER BY id DESC LIMIT 1000'):
        try: tokens+=int(json.loads(row[0]).get('total_tokens',0))
        except (ValueError,TypeError): pass
    errors = portfolio.get('errors',[]) or report.get('errors',[])
    # Only actual running stages animate. Market WAIT is a normal idle state.
    scanning = engine=='RUNNING' and report.get('status')=='SCANNING'
    ai_age = age(ai_activity.get('time'), now)
    reviewing = scanning and ai_activity.get('status')=='REVIEWING' and ai_age is not None and ai_age<90
    modules=[
        dict(id='scanner',name='Market Scanner',role='Market structure',state='WORKING' if scanning else 'WAITING',detail='Scanning market snapshots' if scanning else f'{len(scans)} pairs in latest scan'),
        dict(id='research',name='AI Research',role='Evidence review',state='WORKING' if reviewing else 'COOLDOWN' if retry>now else 'WAITING',detail=('Reviewing '+str(ai_activity.get('pair'))+' · '+str(int(ai_age))+'s') if reviewing else ('Provider retry in '+str(provider['remaining_seconds'])+'s' if retry>now else 'Last provider state: '+provider['state'])),
        dict(id='risk',name='Risk Monitor',role='Account protection',state='BLOCKED' if portfolio.get('halted') or errors else 'MONITORING',detail='Daily limit reached' if portfolio.get('halted') else 'Reconciliation needs attention' if errors else 'Risk checks enabled'),
        dict(id='execution',name='Execution',role='OKX demo positions',state='MONITORING',detail=f'{len(active)} active positions'),
        dict(id='signals',name='Signal Desk',role='Reference tracking',state='MONITORING',detail=f'{sum(s["status"] in ("PENDING","ACTIVE") for s in signals)} live in recent 30 references'),
        dict(id='alerts',name='Alert Delivery',role='Telegram & Discord',state='MONITORING',detail='Latest delivery: '+(activity[0]['status'] if activity else 'no records'))]
    if engine!='RUNNING':
        for m in modules: m['state']=engine
    elif scan_age is None or scan_age>180:
        if not scanning: modules[0]['state']='STALE'
    return dict(time=datetime.fromtimestamp(now,timezone.utc).isoformat(),engine=dict(status=engine,heartbeat_age=heartbeat_age,last_heartbeat=beat.get('time'),phase=report.get('status'),phase_age=age(report.get('time'),now)),
                portfolio=dict(equity=portfolio.get('equity'),day_start=portfolio.get('day_start'),day=portfolio.get('day'),halted=portfolio.get('halted'),active=len(active),max_positions=controls.get('max_positions',5),margin=controls.get('margin'),min_leverage=controls.get('min_leverage'),max_leverage=controls.get('max_leverage'),entries_enabled=controls.get('entries_enabled'),errors=len(errors)),
                positions=positions,scanner=dict(age=scan_age,last_scan=scan_stamp,rows=scans),provider=provider,tokens_24h=tokens,signals=signals,activity=activity,modules=modules,warnings=list(dict.fromkeys(warnings)))


class Dashboard:
    def __init__(self, port=8765, root=RUNTIME_DIR):
        self.root, self.port = root, port
        self.server = self.thread = None

    def start(self):
        root=self.root
        assets=PACKAGE_DIR/'web'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                expected={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
                origin=self.headers.get('Origin')
                if self.headers.get('Host') not in expected or self.headers.get('Sec-Fetch-Site')=='cross-site' or (origin and origin not in {'http://'+h for h in expected}):
                    self.send_error(403);return
                path=urlsplit(self.path).path
                try:
                    if path=='/api/state':
                        body=json.dumps(snapshot(root),allow_nan=False).encode();kind='application/json'
                    elif path in ('/','/app.js','/style.css'):
                        body=(assets/({'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path])).read_bytes()
                        kind={'/':'text/html','/app.js':'text/javascript','/style.css':'text/css'}[path]
                    else:
                        self.send_error(404);return
                except Exception:
                    self.send_error(503,'Monitoring data unavailable');return
                self.send_response(200)
                self.send_header('Content-Type',kind+'; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',self.port),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        if self.server:
            self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    server=Dashboard(args.port)
    try:
        print(json.dumps({'status':'DASHBOARD_RUNNING','url':server.start(),'mode':'READ_ONLY'}),flush=True)
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
    finally: server.close()
    return 0


if __name__=='__main__': main()
