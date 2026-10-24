"""Edit delivered cards from reference history or reconciled OKX execution evidence."""
import copy
import fcntl
import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPSHandler
from hedging_tools.alerts import AlertError, NoRedirect, settings, payload, price
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paths import RUNTIME_DIR
from hedging_tools.risk_engine import number as D


def edit(target, remote_id, message, opener=None):
    if not re.fullmatch(r'[1-9][0-9]*', str(remote_id)):
        raise AlertError('Invalid message ID')
    body = payload(target, message)
    if target['channel'] == 'discord':
        url = target['url'].split('?')[0] + '/messages/' + str(remote_id)
        method = 'PATCH'
        body.pop('username', None)
    else:
        url = target['url'].rsplit('/', 1)[0] + '/editMessageText'
        body['message_id'] = int(remote_id)
        method = 'POST'
    opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=OKXPublic().context)).open
    try:
        with opener(Request(url, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'}, method=method), timeout=10) as response:
            raw = response.read(100001)
        if len(raw) > 100000:
            raise AlertError('Oversized edit response')
        result = json.loads(raw)
        if target['channel'] == 'telegram':
            if result.get('ok') is not True or str(result.get('result', {}).get('message_id')) != str(remote_id):
                raise AlertError('Edit not confirmed')
        elif str(result.get('id')) != str(remote_id):
            raise AlertError('Edit receipt mismatch')
    except HTTPError as exc:
        code = exc.code
        raw = exc.read(100000)
        exc.close()
        if target['channel'] == 'telegram' and code == 400:
            try:
                description = json.loads(raw).get('description', '')
                if description.startswith('Bad Request: message is not modified'):
                    return
                if description.startswith('Bad Request: message to edit not found'):
                    code = 404
            except (ValueError, AttributeError):
                pass
        raise AlertError(f'Card edit HTTP {code}', http_status=code) from None
    except AlertError:
        raise
    except Exception:
        raise AlertError('Card edit network/response error') from None


def progress(reference, position=None, portfolio_stale=False):
    if position is None:
        events = {e['event'] for e in reference.get('events', [])}
        stage = reference.get('stage', 0)
        return dict(scope='Reference price touches', status=reference['status'],
                    hits=[f'TP{i}' in events and stage >= i for i in (1, 2, 3)],
                    stop_hit=reference['status'] in ('SL', 'PROTECTED_STOP'),
                    protected=reference['status'] == 'PROTECTED_STOP', stage=stage,
                    stop=reference['current_stop'], entry=reference['entry'], targets=reference['targets'],
                    quality=reference.get('quality', 'OK'))
    # Only full, terminal fills establish a TP milestone, never a mark-price touch.
    evidence = position.get('execution_evidence', [])
    hits = []
    for i, leg in enumerate(position['legs'], 1):
        rows = [e for e in evidence if e.get('role') == 'TP'+str(i)]
        filled = sum((D(e.get('accFillSz') or 0) for e in rows), D(0))
        hits.append(bool(rows) and filled == D(leg['size']) and all(e.get('state') in ('filled','canceled') for e in rows))
    stopped = any(e.get('role') == 'SL' and D(e.get('accFillSz') or 0) > 0 for e in evidence)
    stop = position.get('exit_stop_trigger') or position.get('current_stop') or position['stop']
    entry = position.get('entry') or position['limit_price']
    protected = stopped and (D(stop)-D(entry))*(1 if position['direction']=='LONG' else -1) >= 0
    status = position['status'] + (' · '+position.get('exit_kind','') if position['status']=='CLOSED' else '')
    return dict(scope='OKX confirmed fills', status=status, hits=hits, stop_hit=stopped,
                protected=protected, stage=2 if hits[0] and hits[1] else 1 if hits[0] else 0,
                stop=stop, entry=entry, targets=[l['price'] for l in position['legs']],
                quality='RECONCILIATION_PENDING' if portfolio_stale or position['status'] not in ('OPEN','CLOSED') else 'OK')


def render(base, state):
    message = copy.deepcopy(base)
    card = message['card']
    card['subtitle'] = 'AVAR4ISE · OKX Demo · '+state['scope']
    for field in card['fields']:
        name = field['name'].removeprefix('⬜ ')
        if name == 'Entry':
            field['value'] = price(state['entry'])
        elif name == 'Stop Loss':
            label = ('PROFIT STOP HIT' if state['stage'] >= 2 else 'BEP HIT') if state['protected'] else 'SL HIT'
            field['name'] = ('✅ '+label) if state['stop_hit'] else '⬜ Stop Loss'
            field['value'] = price(state['stop'])
        elif name in ('TP1','TP2','TP3'):
            index = int(name[-1])-1
            field['name'] = ('✅ '+name+' HIT') if state['hits'][index] else '⬜ '+name
            field['value'] = price(state['targets'][index])
    status = state['status'].replace('_', ' ')
    if state['quality'] != 'OK':
        status += ' · '+state['quality'].replace('_', ' ')
    card['fields'].append({'name':'Status', 'value':status, 'inline':False})
    if state['stage'] and not state['stop_hit'] and state['status'] != 'TP3' and not state['status'].startswith('CLOSED'):
        card['fields'].append({'name':'Protection', 'value':'Current stop: '+price(state['stop']), 'inline':False})
    return message


def read_data(path, query):
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        return [json.loads(row[0]) for row in db.execute(query)]


def update(root=RUNTIME_DIR, targets=None, sender=edit, now=None):
    now = time.time() if now is None else now
    targets = settings() if targets is None else targets
    database = root/'alerts.sqlite3'
    if not database.exists() or not targets:
        return {'updated':0, 'errors':[]}
    destinations = {hashlib.sha256((t['url']+t.get('chat_id','')).encode()).hexdigest():t for t in targets}
    # Unreadable portfolio aborts updates rather than substituting reference touches for fills.
    references = {s['id']:s for s in read_data(root/'signal-history.sqlite3','SELECT data FROM tracked_signals')}
    portfolios = read_data(root/'portfolio.sqlite3','SELECT data FROM portfolio WHERE id=1')
    portfolio = portfolios[0] if portfolios else {}
    with closing(sqlite3.connect(database)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_messages'").fetchone():
            return {'updated':0,'errors':[]}
        rows = db.execute('SELECT id,fingerprint,signal_id,remote_id,base,digest,failures FROM alert_messages WHERE disabled=0 AND retry<=? ORDER BY retry,id',(now,)).fetchall()
    updated, attempted, errors = 0, 0, []
    for key, fingerprint, signal_id, remote_id, raw, old_digest, failures in rows:
        if fingerprint not in destinations or signal_id not in references:
            continue
        base = json.loads(raw)
        matching = [p for p in portfolio.get('positions', []) if p.get('signal_id') == signal_id or p.get('entry_evidence',{}).get('packet_id') == base.get('packet_id')]
        if len(matching) > 1:
            continue
        state = progress(references[signal_id], matching[0] if matching else None, bool(portfolio.get('errors')))
        message = render(base, state)
        digest = hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
        if digest == old_digest:
            continue
        attempted += 1
        disabled = 0
        try:
            sender(destinations[fingerprint], remote_id, message)
            new_digest, retry, failures = digest, now, 0
            updated += 1
        except Exception as exc:
            failures += 1
            disabled = int(isinstance(exc,AlertError) and exc.http_status in (403,404))
            retry = now+min(3600,60*2**min(failures-1,6))
            new_digest = old_digest
            errors.append({'channel':destinations[fingerprint]['channel'], 'reason':'Card edit failed', 'disabled':bool(disabled)})
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('UPDATE alert_messages SET digest=?,retry=?,failures=?,disabled=? WHERE id=?',
                       (new_digest,retry,failures,disabled,key))
        if attempted >= 10:
            break
    return {'updated':updated, 'errors':errors}


def main():
    with (RUNTIME_DIR/'.card-updates.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            result = update()
        except Exception:
            result = {'updated':0,'errors':[{'reason':'Card update data unavailable'}]}
        (RUNTIME_DIR/'card-updates-output.json').write_text(json.dumps(result))


if __name__ == '__main__':
    main()
