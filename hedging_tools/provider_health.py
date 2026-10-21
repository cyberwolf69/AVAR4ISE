"""Persistent provider cooldowns and usage; no automatic retries or model switching."""
from contextlib import closing
import hashlib
import json
import sqlite3
import time
import uuid


class ProviderHealth:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS provider_health (profile TEXT PRIMARY KEY, failures INTEGER, retry REAL, reason TEXT, lease TEXT, lease_until REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS provider_usage (id INTEGER PRIMARY KEY, profile TEXT, at REAL, status TEXT, usage TEXT)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def profile(config):
        # Only the digest is persisted; changing credentials/model permits a fresh attempt.
        raw = [config.get(k,'') for k in ('AI_BASE_URL','AI_MODEL','AI_API_KEY')]
        return hashlib.sha256(json.dumps(raw).encode()).hexdigest()

    def claim(self, config):
        profile, now = self.profile(config), self.clock()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT retry,reason,lease_until FROM provider_health WHERE profile=?',(profile,)).fetchone()
            if row and max(row[0],row[2]) > now:
                return None, {'reason':row[1] if row[0]>now else 'REQUEST_IN_PROGRESS',
                              'retry_at':max(row[0],row[2])}
            token = uuid.uuid4().hex
            db.execute("INSERT OR IGNORE INTO provider_health VALUES (?,0,0,'READY','',0)",(profile,))
            db.execute('UPDATE provider_health SET lease=?,lease_until=? WHERE profile=?',(token,now+90,profile))
            return (profile,token), None

    def finish(self, claim, error=None, usage=None):
        profile, token = claim
        now = self.clock()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT failures,lease FROM provider_health WHERE profile=?',(profile,)).fetchone()
            if not row or row[1]!=token:
                return
            failures = row[0]+1 if error else 0
            code = getattr(error,'http_status',None)
            if code == 402:
                reason,delay = 'PAYMENT_OR_ACCESS_REQUIRED', (15,30,60)[failures-1] if failures<=3 else 900
            elif code in (401,403):
                reason,delay = 'AUTH_OR_PERMISSION_REQUIRED',1800
            elif code == 429:
                reason,delay = 'RATE_OR_QUOTA_LIMIT',min(1800,60*2**min(failures-1,5))
            elif code in (400,404):
                reason,delay = 'REQUEST_OR_MODEL_CONFIGURATION',1800
            elif error:
                reason,delay = 'PROVIDER_REQUEST_FAILED', (15,30,60)[failures-1] if failures<=3 else 900
            else:
                reason,delay = 'HEALTHY',0
            db.execute('UPDATE provider_health SET failures=?,retry=?,reason=?,lease=?,lease_until=0 WHERE profile=?',
                       (failures,now+delay,reason,'',profile))
            db.execute('INSERT INTO provider_usage(profile,at,status,usage) VALUES (?,?,?,?)',
                       (profile,now,reason,json.dumps(usage or {})))

    def status(self, config):
        with closing(self.connect()) as db:
            row=db.execute('SELECT failures,retry,reason,lease_until FROM provider_health WHERE profile=?',
                           (self.profile(config),)).fetchone()
        return dict(zip(('failures','retry_at','reason','lease_until'),row)) if row else {'reason':'NOT_CALLED'}
