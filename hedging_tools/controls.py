"""Persistent owner controls, bounded by independent hard risk limits."""
from contextlib import closing, contextmanager
import fcntl
import json
import secrets
import sqlite3
import time
from hedging_tools.paths import RUNTIME_DIR
from hedging_tools.risk_engine import number as D

DEFAULT = {'entries_enabled':True,'min_leverage':10,'max_leverage':30,'margin':'5','max_positions':5,'revision':0}


def validate(value):
    if set(value) != set(DEFAULT):
        raise ValueError('Invalid control fields')
    if type(value['entries_enabled']) is not bool:
        raise ValueError('Invalid trading switch')
    if any(type(value[k]) is not int for k in ('min_leverage','max_leverage','max_positions','revision')):
        raise ValueError('Integer settings required')
    if not 10 <= value['min_leverage'] <= value['max_leverage'] <= 30:
        raise ValueError('Leverage must be within 10–30x')
    if not 1 <= D(value['margin']) <= 5:
        raise ValueError('Margin must be 1–5 USDT')
    if not 1 <= value['max_positions'] <= 5:
        raise ValueError('Maximum positions must be 1–5')
    return value


class Controls:
    def __init__(self,path=RUNTIME_DIR/'controls.sqlite3'):
        self.path=path

    @contextmanager
    def locked(self):
        with self.path.with_suffix('.lock').open('a') as file:
            fcntl.flock(file,fcntl.LOCK_EX)
            yield

    def initialize(self):
        with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1),data TEXT)')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1,?)',(json.dumps(DEFAULT),))
            db.execute('CREATE TABLE IF NOT EXISTS confirmations (token TEXT PRIMARY KEY,owner INTEGER,expires REAL,data TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY,at REAL,owner INTEGER,before TEXT,after TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER)')

    def read(self):
        if not self.path.exists():
            return dict(DEFAULT)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT data FROM settings WHERE id=1').fetchone()
        if not row:
            raise ValueError('Control settings unavailable; entries blocked')
        return validate(json.loads(row[0]))

    def propose(self,owner,changes,now=None):
        now=time.time() if now is None else now
        with self.locked():
            self.initialize()
            before=self.read();after=validate({**before,**changes})
            token=secrets.token_hex(6)
            with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
                db.execute('DELETE FROM confirmations WHERE owner=? OR expires<?',(owner,now))
                db.execute('INSERT INTO confirmations VALUES (?,?,?,?)',
                           (token,owner,now+120,json.dumps({'before':before,'after':after})))
        return token,before,after

    def confirm(self,owner,token,now=None):
        now=time.time() if now is None else now
        with self.locked():
            self.initialize()
            with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
                row=db.execute('SELECT expires,data FROM confirmations WHERE token=? AND owner=?',(token,owner)).fetchone()
                if not row or now>=row[0]:
                    raise ValueError('Invalid or expired confirmation; submit the settings again')
                change=json.loads(row[1]);current=self.read()
                if current!=change['before']:
                    raise ValueError('Settings have changed; submit the request again')
                after=validate({**change['after'],'revision':current['revision']+1})
                db.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(after),))
                db.execute('INSERT INTO audit(at,owner,before,after) VALUES (?,?,?,?)',
                           (now,owner,json.dumps(current),json.dumps(after)))
                db.execute('DELETE FROM confirmations WHERE token=?',(token,))
        return after

    def offset(self):
        self.initialize()
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT value FROM cursor WHERE id=1').fetchone()
        return row[0] if row else 0

    def acknowledge(self,update_id):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('INSERT INTO cursor VALUES (1,?) ON CONFLICT(id) DO UPDATE SET value=MAX(value,excluded.value)',(update_id+1,))
