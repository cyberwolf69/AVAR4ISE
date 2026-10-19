"""Owner-only Telegram remote; runs independently of the trading process."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import re
import sqlite3
import time
from urllib.request import Request, build_opener, HTTPSHandler
from hedging_tools.alerts import NoRedirect
from hedging_tools.controls import Controls
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paths import PROJECT_ROOT, RUNTIME_DIR
from hedging_tools.risk_engine import number as D

HELP = '''AVAR4ISE · OKX DEMO
/status — heartbeat, scan, settings
/positions — active positions
/history — last 10 closed positions
/performance — actual trading results
/settings — current settings
/leverage 10 20 — automatic leverage range
/margin 5 — margin 1–5 USDT
/slots 5 — maximum 1–5 positions
/pause — pause new entries
/resume — allow entries (subject to risk checks)
Changes require /confirm CODE within 2 minutes.
Open positions remain managed. The remote cannot start a stopped run process.'''
KEYBOARD={'keyboard':[['/status','/settings'],['/positions','/history'],['/performance','/help'],['/pause','/resume']],
          'resize_keyboard':True}
COMMANDS=[{'command':command,'description':description} for command,description in (
    ('start','Home and remote buttons'),
    ('menu','Full command list'),
    ('status','Engine status and strategy equity'),
    ('positions','Open positions and TP/SL levels'),
    ('history','Recent trade history'),
    ('performance','Actual trading results'),
    ('settings','Current settings'),
    ('leverage','Leverage range: /leverage 10 20'),
    ('margin','Margin per position: /margin 5'),
    ('slots','Maximum positions: /slots 5'),
    ('pause','Pause new entries'),
    ('resume','Allow entries within risk limits'),
    ('confirm','Confirm changes: /confirm CODE'),
    ('help','Remote help'),
)]


def settings(path=PROJECT_ROOT/'.env'):
    values={}
    if path.exists():
        for line in path.read_text().splitlines():
            key,sep,value=line.partition('=')
            if sep and not line.lstrip().startswith('#'):
                values[key.strip()]=value.strip().strip('\"\'')
    if values.get('TELEGRAM_REMOTE_ENABLED','false').lower()!='true':
        raise ValueError('Set TELEGRAM_REMOTE_ENABLED=true and TELEGRAM_OWNER_ID in .env')
    owner=values.get('TELEGRAM_OWNER_ID','')
    token=values.get('TELEGRAM_BOT_TOKEN','')
    if not owner.isdigit() or int(owner)<=0 or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):
        raise ValueError('A numeric TELEGRAM_OWNER_ID and TELEGRAM_BOT_TOKEN are required')
    return int(owner),token


class API:
    def __init__(self,token):
        self.base='https://api.telegram.org/bot'+token+'/'
        self.opener=build_opener(NoRedirect(),HTTPSHandler(context=OKXPublic().context)).open

    def call(self,method,body):
        if method not in ('getUpdates','sendMessage','setMyCommands','setChatMenuButton'):
            raise ValueError('Unsupported remote API method')
        request=Request(self.base+method,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:
            with self.opener(request,timeout=35) as response:
                data=json.load(response)
            if data.get('ok') is not True:
                raise ValueError('Rejected')
            return data['result']
        except Exception:
            # urllib exceptions can contain token-bearing URLs. Never print them.
            raise RuntimeError('Telegram request failed; check network/token or conflicting poller/webhook') from None


class CommandMenu:
    """Register once per startup; retry failures without blocking message polling."""
    def __init__(self):
        self.ready=False
        self.next_attempt=0

    def tick(self,api,owner,now=None):
        now=time.monotonic() if now is None else now
        if self.ready or now<self.next_attempt:
            return None
        try:
            if api.call('setMyCommands',{'commands':COMMANDS,
                    'scope':{'type':'chat','chat_id':owner},'language_code':''}) is not True:
                raise ValueError('Menu rejected')
            if api.call('setChatMenuButton',{'chat_id':owner,
                    'menu_button':{'type':'commands'}}) is not True:
                raise ValueError('Menu button rejected')
        except Exception:
            self.next_attempt=now+60
            return {'status':'COMMAND_MENU_RETRY','reason':'Menu registration pending; check network/token and open the bot chat with /start'}
        self.ready=True
        return {'status':'COMMAND_MENU_READY','scope':'OWNER_PRIVATE_CHAT_ONLY'}


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError,ValueError):
        return {}


def portfolio(root):
    path=root/'portfolio.sqlite3'
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT data FROM portfolio WHERE id=1').fetchone()
    return json.loads(row[0]) if row else {}


def format_settings(s):
    return (f"New entries: {'ON' if s['entries_enabled'] else 'OFF'}\n"
            f"Automatic leverage: {s['min_leverage']}–{s['max_leverage']}x\n"
            f"Maximum margin: {s['margin']} USDT\nMaximum positions: {s['max_positions']}\n"
            'Fixed limits: entry risk ≤1 USDT; daily loss 10%.')


def report(command,controls,root=RUNTIME_DIR,now=None):
    now=time.time() if now is None else now
    if command=='/settings':
        return format_settings(controls.read())
    state=portfolio(root);positions=state.get('positions',[])
    if command=='/status':
        heartbeat=read_json(root/'engine-heartbeat.json')
        stamp=heartbeat.get('time')
        age=now-datetime.fromisoformat(stamp).timestamp() if stamp else None
        engine=('RUNNING' if heartbeat.get('status')=='RUNNING' and age is not None and 0<=age<=30 else
                'STOPPED' if heartbeat.get('status')=='STOPPED' else 'UNKNOWN / stale heartbeat')
        research=read_json(root/'research-output.json')
        latest=read_json(root/'portfolio-output.json')
        return (f"Engine: {engine}\nHeartbeat age: {round(age,1) if age is not None else 'N/A'} seconds\n"
                f"Latest report: {latest.get('status','N/A')} @ {latest.get('time','N/A')}\n"
                f"Strategy equity: {state.get('equity','N/A')} USDT\nDaily halt: {state.get('halted','N/A')}\n"
                f"Open positions: {sum(p['status']!='CLOSED' for p in positions)}\n"
                f"Management errors: {len(state.get('errors',[]))}\n"
                f"Latest AI: {research.get('ai_status','N/A')}; calls: {research.get('ai_calls','N/A')}\n"
                +format_settings(controls.read())+'\nStatus reflects saved snapshots, not a fresh connection test.')
    if command in ('/positions','/history'):
        selected=[p for p in positions if (p['status']=='CLOSED')==(command=='/history')]
        if command=='/history':
            selected=sorted(selected,key=lambda p:p.get('closed_at',0))[-10:]
        lines=[f"{'Closed positions' if command=='/history' else 'Open positions'} · {len(selected)}"]
        for p in selected:
            lines.append(f"{p['pair']} {p.get('direction','?')} {p['status']} | {p.get('leverage','?')}x\n"
                         f"Margin {p.get('margin',p.get('margin_upper_bound','?'))}; entry {p.get('entry',p.get('limit_price','?'))}\n"
                         f"Initial SL {p.get('stop','?')}; Latest SL {(p.get('stop_history') or [{}])[-1].get('to',p.get('stop','?'))}; PnL incl fee/excl funding {p.get('pnl','0')}; UPL {p.get('upl','0')}\n"
                         f"TP: {', '.join(str(leg.get('price','?')) for leg in p.get('legs',[]))}\n"
                         f"Exit {p.get('exit_kind','-')} | closed_ms {p.get('closed_at','-')}")
        return '\n'.join(lines)
    if command=='/performance':
        closed=[p for p in positions if p['status']=='CLOSED' and p.get('exit_kind')!='NO_FILL']
        realized=sum((D(p.get('pnl',0)) for p in positions),D(0))
        funding=sum((D(v) for v in state.get('funding',{}).values()),D(0))
        wins=sum(D(p.get('pnl',0))>0 for p in closed)
        winrate=f'{wins/len(closed)*100:.1f}%' if closed else 'N/A'
        return (f"Actual strategy trades · OKX Demo\nClosed positions: {len(closed)}\n"
                f"Win rate after fees, before funding: {winrate}\n"
                f"Realized PnL incl fees/excl funding (including partial exits): {realized}\n"
                f"Recorded funding: {funding}\nNet realized + funding: {realized+funding}\n"
                f"Equity incl unrealized: {state.get('equity','N/A')}\n"
                'Historical drawdown: a complete equity curve is not yet available.\nShadow/reference results are excluded; the full demo balance is not strategy equity.')
    return HELP


def handle(update,owner,controls,root=RUNTIME_DIR,now=None):
    now=time.time() if now is None else now
    message=update.get('message',{})
    sender=message.get('from',{});chat=message.get('chat',{})
    if sender.get('id')!=owner or sender.get('is_bot') or chat.get('type')!='private' or chat.get('id')!=owner:
        return None
    if 'forward_origin' in message or not -5<=now-message.get('date',0)<=120:
        return None
    text=message.get('text','')
    if not isinstance(text,str) or len(text)>200:
        return None
    parts=text.strip().split()
    if not parts:
        return None
    command=parts[0].split('@')[0].lower()
    try:
        if command=='/confirm' and len(parts)==2:
            return 'Saved for subsequent entries.\n'+format_settings(controls.confirm(owner,parts[1],now))
        changes=None
        if command in ('/pause','/resume') and len(parts)==1:
            changes={'entries_enabled':command=='/resume'}
        elif command=='/leverage' and len(parts)==3:
            changes={'min_leverage':int(parts[1]),'max_leverage':int(parts[2])}
        elif command=='/margin' and len(parts)==2:
            changes={'margin':str(D(parts[1]))}
        elif command=='/slots' and len(parts)==2:
            changes={'max_positions':int(parts[1])}
        if changes is not None:
            token,before,after=controls.propose(owner,changes,now)
            delta='\n'.join(f'{key}: {before[key]} → {after[key]}' for key in changes)
            return f'{delta}\nOpen positions remain managed.\nConfirm within 2 minutes: /confirm {token}'
        if command in ('/status','/settings','/positions','/history','/performance') and len(parts)==1:
            return report(command,controls,root,now)
        return HELP
    except (ValueError,ArithmeticError):
        return 'Settings/confirmation rejected. Limits: leverage 10–30x, margin 1–5 USDT, positions 1–5. Submit the command again for a new confirmation.'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Check local configuration only; no network calls')
    args=parser.parse_args()
    try:
        owner,token=settings()
    except ValueError as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)}));return 1
    if args.check:
        print(json.dumps({'status':'READY','scope':'OWNER_PRIVATE_CHAT_ONLY'}));return 0
    with (RUNTIME_DIR/'.telegram-remote.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'BLOCKED','reason':'Remote already running'}));return 1
        controls=Controls();controls.initialize();api=API(token);menu=CommandMenu()
        print(json.dumps({'status':'REMOTE_RUNNING','mode':'OKX_DEMO'}),flush=True)
        try:
            while True:
                menu_status=menu.tick(api,owner)
                if menu_status:
                    print(json.dumps(menu_status),flush=True)
                try:
                    offset=controls.offset()
                    updates=api.call('getUpdates',{'offset':offset,'timeout':20,'allowed_updates':['message']})
                    for update in updates:
                        if update['update_id']<offset:
                            continue
                        # At-most-once commands: persist before mutation; a crash can lose a reply,
                        # but cannot replay an old confirmation after restart.
                        controls.acknowledge(update['update_id'])
                        reply=handle(update,owner,controls)
                        if reply:
                            api.call('sendMessage',{'chat_id':owner,'text':reply[:4000],'reply_markup':KEYBOARD})
                except Exception:
                    print(json.dumps({'status':'REMOTE_RETRY','reason':'Request or local state unavailable; inspect configuration/network'}),flush=True)
                    time.sleep(5)
        except KeyboardInterrupt:
            print(json.dumps({'status':'REMOTE_STOPPED','engine':'unchanged'}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
