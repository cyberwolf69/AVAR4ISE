"""Durable owner notifications; network delivery never runs in the trading loop."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import re
import sqlite3
import time
from hedging_tools.paths import PROJECT_ROOT, RUNTIME_DIR


class EngineEvents:
    def __init__(self, path=RUNTIME_DIR/'engine-events.sqlite3', env=PROJECT_ROOT/'.env'):
        self.path = path
        self.secrets = []
        if env.exists():
            for line in env.read_text().splitlines():
                key, sep, value = line.partition('=')
                value = value.strip().strip('\"\'')
                if sep and any(k in key.upper() for k in ('KEY','TOKEN','SECRET','PASSPHRASE','WEBHOOK')) and value:
                    self.secrets.append(value)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created REAL, text TEXT, status TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS latest(topic TEXT PRIMARY KEY, body TEXT, sent REAL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=0.2)

    def emit(self, topic, body, now=None, repeat=900):
        now = time.time() if now is None else now
        body = str(body)
        for secret in self.secrets:
            body = body.replace(secret, '[REDACTED]')
        body = re.sub(r'https?://\S+', '[URL omitted]', body)[:3500]
        with closing(self.connect()) as db, db:
            old = db.execute('SELECT body,sent FROM latest WHERE topic=?', (topic,)).fetchone()
            if old and old[0] == body and now-old[1] < repeat:
                return
            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
            db.execute('INSERT INTO events(created,text,status) VALUES (?,?,?)',
                       (now, f'AVAR4ISE · {topic}\n{stamp}\n{body}', 'PENDING'))
            db.execute('INSERT OR REPLACE INTO latest VALUES (?,?,?)', (topic,body,now))

    def report(self, report):
        status = report.get('status','UNKNOWN')
        if status == 'SCANNING':
            return  # The completed-cycle summary includes scan activity.
        if status == 'SCAN_COMPLETE':
            decisions = report.get('decisions',[])
            counts = Counter(d.get('status','UNKNOWN') for d in decisions)
            reasons = Counter(r.split(':')[0] for d in decisions for r in d.get('screen_reasons',[]))
            body = f"Cycle {report.get('cycle')} | {len(decisions)} pairs\nDecisions: {dict(counts)}\nScreen: {dict(reasons)}"
            for d in decisions:
                if d.get('status') != 'WAIT':
                    body += '\n'+json.dumps(d)
            body += '\nAlerts: '+json.dumps(report.get('alerts'))
            self.emit('SCAN_COMPLETE',body)
            ai = report.get('ai', {})
            provider = ai.get('provider_health')
            if provider and provider.get('reason') not in ('HEALTHY','NOT_CALLED'):
                self.emit('AI_PROVIDER_STATE',json.dumps(provider))
            if ai.get('errors'):
                self.emit('AI_HEALTH',json.dumps(ai['errors'])+self.ai_hint(ai['errors']))
            elif ai.get('validated',0):
                self.emit('AI_HEALTH','AI responses validated successfully.')
            # No AI call is not evidence that a prior AI failure recovered.
            return
        if status in ('MANAGING','ENTRY_BLOCKED'):
            # Exclude fluctuating equity from the health fingerprint to avoid tick spam.
            self.emit('ENGINE_HEALTH',json.dumps({k:report.get(k) for k in
                ('status','active_positions','max_positions','daily_halted','errors','reason')}))
            return
        self.emit(status,json.dumps(report))

    @staticmethod
    def ai_hint(errors):
        text = str(errors)
        if 'HTTP 402' in text:
            return '\nHTTP 402: Payment Required. Check router credits, plan and model access. Exact provider cause is unconfirmed; this is not proof of a rate limit.'
        if 'HTTP 429' in text:
            return '\nHTTP 429: Too Many Requests. Check provider rate limits/quota.'
        return ''

    def positions(self, state):
        for p in state.get('positions',[]):
            body = {k:p.get(k) for k in ('id','pair','direction','status','entry','stop','current_stop','stage','remaining','pnl','exit_kind','stop_history','legs')}
            self.emit('POSITION '+str(p.get('id')),json.dumps(body),repeat=float('inf'))

    def drain(self, api, owner, limit=3):
        for _ in range(limit):
            with closing(self.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT id,text FROM events WHERE status='PENDING' ORDER BY id LIMIT 1").fetchone()
                if not row:
                    return
                # Claim before sending; an ambiguous timeout must not duplicate a message.
                db.execute("UPDATE events SET status='UNCONFIRMED' WHERE id=?", (row[0],))
            result = api.call('sendMessage', {'chat_id':owner,'text':row[1],
                                             'link_preview_options':{'is_disabled':True}})
            if not isinstance(result,dict) or not result.get('message_id'):
                raise RuntimeError('Owner notification delivery unconfirmed')
            with closing(self.connect()) as db, db:
                db.execute("UPDATE events SET status='SENT' WHERE id=?",(row[0],))
