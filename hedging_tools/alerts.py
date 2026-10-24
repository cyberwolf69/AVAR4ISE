"""Opt-in signal notifications. Destinations and credentials are never journaled."""
import argparse
import hashlib
import html
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from zoneinfo import ZoneInfo
from hedging_tools.analyst import validate_proposal, make_packet
from hedging_tools.okx_public import OKXPublic
from hedging_tools.paths import PROJECT_ROOT, RUNTIME_DIR
from hedging_tools.risk_engine import number
from hedging_tools.position_policy import targets, BRAND_URL, BRAND_TEXT


class AlertError(Exception):
    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AlertError('Redirect refused')


def settings(path=PROJECT_ROOT / '.env'):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and not line.lstrip().startswith('#'):
                values[key.strip()] = value.strip().strip('\"\'')
    if values.get('ALERTS_ENABLED', 'false').lower() != 'true':
        return []
    channels = list(dict.fromkeys(x.strip() for x in values.get('ALERT_CHANNELS', '').split(',') if x.strip()))
    if not channels or set(channels) - {'discord', 'telegram'}:
        raise AlertError('ALERT_CHANNELS must contain discord and/or telegram')
    targets = []
    for channel in channels:
        if channel == 'discord':
            url = values.get('DISCORD_WEBHOOK_URL', '')
            parts = urlsplit(url)
            if (parts.scheme != 'https' or parts.netloc != 'discord.com' or parts.query or parts.fragment
                    or not re.fullmatch(r'/api(?:/v10)?/webhooks/[0-9]+/[A-Za-z0-9_-]+', parts.path)):
                raise AlertError('Fill a valid Discord webhook URL in local .env')
            targets.append({'channel': channel, 'url': url + '?wait=true'})
        else:
            token, chat = values.get('TELEGRAM_BOT_TOKEN', ''), values.get('TELEGRAM_CHAT_ID', '')
            if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) or not re.fullmatch(r'-?[0-9]+|@[A-Za-z0-9_]+', chat):
                raise AlertError('Fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in local .env')
            targets.append({'channel': channel, 'url': f'https://api.telegram.org/bot{token}/sendMessage', 'chat_id': chat})
    return targets


def clean(value, limit=600):
    # No unsolicited mentions or Markdown links from untrusted AI/news text.
    value = str(value).replace('@', '＠').replace('`', "'")
    value = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)
    value = re.sub(r'https?://\S+', '[link omitted]', value)
    return value[:limit]


def brief(value, limit=650):
    """Keep prose readable without inventing a summary or cutting a word in half."""
    value = clean(value, 4000)
    value = value.replace('structure_bos_retest_v1', 'BOS + retest')
    def timestamp(match):
        try:
            return datetime.fromtimestamp(int(match[0])/1000, ZoneInfo('Asia/Jakarta')).strftime('%d %b %H:%M WIB')
        except (ValueError, OSError, OverflowError):
            return '[timestamp]'
    value = re.sub(r'\b1[0-9]{12}\b', timestamp, value)
    value = ' '.join(value.split())
    if len(value) <= limit:
        return value
    return value[:limit-1].rsplit(' ', 1)[0].rstrip(',;:') + '…'


def price(value):
    text = format(number(value), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def markdown(value):
    return re.sub(r'([\\*_~|<>\[\]])', r'\\\1', str(value))


def signal_identity(packet, action):
    bucket = int(datetime.fromisoformat(packet['created_at']).timestamp()) // 900
    return f"signal:{packet['pair']}:{action}:{bucket}"


def signal_message(envelope, proposal, now=None, basis="AI"):
    validated = validate_proposal(proposal, envelope, now)
    signal = validated['signal']
    if signal['action'] == 'WAIT':
        return None
    packet, candidate = envelope['input'], envelope['input']['market']['candidate']
    entry, stop = number(candidate['entry']), number(signal['stop'])
    sign = 1 if signal['action'] == 'LONG' else -1
    distance = (entry - stop) * sign
    if entry <= 0 or stop <= 0 or distance <= 0:
        raise ValueError('Invalid alert price references')
    tp1, tp2, tp3 = targets(entry, stop, signal["action"])
    expiry = datetime.fromisoformat(signal['expires_at']).astimezone(ZoneInfo('Asia/Jakarta'))
    sources = [a['source'] for a in packet['news'] if a['id'] in proposal['source_ids']]
    text = (f"{signal['action']} SIGNAL · {signal['pair']}\n"
            f"AVAR4ISE · OKX DEMO\n\n"
            f"Reference entry: {entry}\nReference stop: {stop}\n"
            f"TP1: {tp1}\n"
            f"TP2: {tp2}\n"
            f"TP3: {tp3}\n"
            "TP1 → SL breakeven + estimated costs · TP2 → lock in profit\n"
            "Strategy limits: margin max. 5 USDT · risk max. 1 USDT\n"
            "Strategy leverage: 10–30x\n\n"
            f"{basis} rationale: {clean(proposal['reason'])}\n"
            f"Invalidation: {clean(proposal['invalidation'], 350)}\n"
            f"Headline sources: {clean(', '.join(dict.fromkeys(sources)) or 'Unavailable; technical setup', 150)}\n"
            )
    event_id = signal_identity(packet, signal['action'])
    card = {
        'title': f"{'🟢' if sign == 1 else '🔴'} {signal['action']} SIGNAL · {signal['pair'].removesuffix('-SWAP')}",
        'subtitle': 'AVAR4ISE · OKX Demo · Reference levels',
        'fields': [
            {'name': 'Entry', 'value': price(entry), 'inline': True, 'code': True},
            {'name': '⬜ Stop Loss', 'value': f'{price(stop)}\n{(stop-entry)/entry*100:+.2f}%', 'inline': True, 'code': True},
        ]}
    for i, tp in enumerate((tp1,tp2,tp3), 1):
        move = (tp-entry)/entry*100
        card['fields'].append({'name': f'⬜ TP{i}',
                               'value': f'{price(tp)}\n{move:+.2f}%', 'inline': True, 'code': True})
    if candidate.get('setup_model') == 'structure_bos_retest_v1':
        card['subtitle'] += ' · 15m entry / 1H structure'
    card['fields'].extend([
        {'name': 'Technical Rationale' if basis == 'TECHNICAL' else '🧠 AI Rationale', 'value': brief(proposal['reason']), 'inline': False},
        {'name': 'Invalidation', 'value': brief(proposal['invalidation'], 300), 'inline': False},
        {'name': 'News Context', 'value': brief(', '.join(dict.fromkeys(sources)) or 'Unavailable · technical setup', 150), 'inline': False},
    ])
    return {'id': event_id, 'packet_id': packet['packet_id'], 'text': text, 'card': card, 'direction': signal['action'], 'expires_at': signal['expires_at']}


def payload(target, message):
    card = message.get('card')
    if target['channel'] == 'discord':
        embed = {'title': card['title'] if card else 'AVAR4ISE · OKX Demo',
                 'description': card['subtitle'] if card else message['text'],
                 'color': 3066993 if message.get('direction') == 'LONG' else 15158332}
        if card:
            fields = []
            for field in card['fields']:
                value = markdown(field['value'])
                if field.get('code'):
                    first, sep, rest = value.partition('\n')
                    value = f'`{first}`' + ('\n'+rest if sep else '')
                fields.append({'name':field['name'], 'value':value, 'inline':field['inline']})
            fields.append({'name':'\u200b', 'value':f'Powered by [{BRAND_TEXT}]({BRAND_URL})', 'inline':False})
            embed['fields'] = fields
        else:
            embed['description'] += f'\n\nPowered by [{BRAND_TEXT}]({BRAND_URL})'
        return {'username':'AVAR4ISE', 'allowed_mentions':{'parse':[]}, 'embeds':[embed]}
    if card:
        sections = [f"<b>{html.escape(card['title'])}</b>\n{html.escape(card['subtitle'])}"]
        for field in card['fields']:
            value = html.escape(field['value'])
            if field.get('code'):
                first, sep, rest = value.partition('\n')
                value = f'<code>{first}</code>' + ('\n'+rest if sep else '')
            sections.append(f"<b>{html.escape(field['name'])}</b>\n{value}")
        text = '\n\n'.join(sections)
    else:
        text = html.escape(message['text'])
    return {'chat_id':target['chat_id'], 'parse_mode':'HTML',
            'text':text+f'\n\nPowered by <a href="{BRAND_URL}">{BRAND_TEXT}</a>',
            'link_preview_options':{'is_disabled':True}}


def send(target, message, opener=None):
    opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=OKXPublic().context)).open
    request = Request(target['url'], data=json.dumps(payload(target, message)).encode(),
                      headers={'Content-Type': 'application/json', 'User-Agent': 'AVAR4ISE/0.1'}, method='POST')
    try:
        with opener(request, timeout=10) as response:
            raw = response.read(100001)
        if len(raw) > 100000:
            raise AlertError('Oversized response')
        result = json.loads(raw)
        if target['channel'] == 'telegram':
            if result.get('ok') is not True or not result.get('result', {}).get('message_id'):
                raise AlertError('Telegram did not confirm message')
        elif not result.get('id'):
            raise AlertError('Discord did not confirm message')
        remote_id = result['result']['message_id'] if target['channel'] == 'telegram' else result['id']
        if not re.fullmatch(r'[1-9][0-9]*', str(remote_id)):
            raise AlertError('Invalid message ID')
        return str(remote_id)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise AlertError(f'HTTP {code}; delivery not confirmed') from None
    except AlertError:
        raise
    except Exception:
        # URLError/exception text can include URLs containing secrets.
        raise AlertError('Network or response error; delivery unknown') from None


def deliver(targets, message, database=RUNTIME_DIR / 'alerts.sqlite3', sender=send):
    results = []
    for target in targets:
        if message.get('expires_at') and datetime.now(timezone.utc) >= datetime.fromisoformat(message['expires_at']):
            results.append({'channel': target['channel'], 'status': 'EXPIRED'})
            continue
        fingerprint = hashlib.sha256((target['url'] + target.get('chat_id', '')).encode()).hexdigest()
        key = hashlib.sha256((fingerprint + message['id']).encode()).hexdigest()
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS alert_deliveries (id TEXT PRIMARY KEY, channel TEXT, created_at TEXT, status TEXT, detail TEXT)')
            cursor = db.execute('INSERT OR IGNORE INTO alert_deliveries VALUES (?,?,?, ?,NULL)',
                                (key, target['channel'], datetime.now(timezone.utc).isoformat(), 'PENDING'))
            claimed = cursor.rowcount == 1
        if not claimed:
            results.append({'channel': target['channel'], 'status': 'DUPLICATE_SUPPRESSED'})
            continue
        status, detail = 'SENT', None
        try:
            remote_id = sender(target, message)
            if message.get('card') and isinstance(remote_id, (str, int)) and re.fullmatch(r'[1-9][0-9]*', str(remote_id)):
                with closing(sqlite3.connect(database)) as db, db:
                    db.execute('CREATE TABLE IF NOT EXISTS alert_messages (id TEXT PRIMARY KEY, fingerprint TEXT, signal_id TEXT, remote_id TEXT, base TEXT, digest TEXT, retry REAL, failures INTEGER, disabled INTEGER)')
                    db.execute('INSERT OR IGNORE INTO alert_messages VALUES (?,?,?,?,?,?,0,0,0)',
                               (key, fingerprint, message['id'], str(remote_id), json.dumps(message), 'INITIAL'))
        except Exception:
            status, detail = 'UNCONFIRMED', 'Delivery failed or unknown; no automatic resend'
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('UPDATE alert_deliveries SET status=?,detail=? WHERE id=?', (status, detail, key))
        results.append({'channel': target['channel'], 'status': status})
    return results


def notify_signals(packets, analyses):
    try:
        targets = settings()
        if not targets:
            return {'status': 'DISABLED', 'deliveries': []}
        deliveries = []
        for envelope, analysis in zip(packets, analyses):
            if analysis.get('status') == 'VALIDATED':
                message = signal_message(envelope, analysis['proposal'])
            elif analysis.get('technical_proposal') and analysis.get('publication_basis') == 'TECHNICAL':
                from hedging_tools.pending_setups import technical_proposal
                # Recheck freshness and reconstruct locally instead of trusting stored text.
                message = signal_message(envelope, technical_proposal(envelope), basis='TECHNICAL')
            else:
                continue
            if message:
                deliveries.extend(deliver(targets, message))
        return {'status': 'CHECKED', 'deliveries': deliveries}
    except Exception:
        return {'status': 'ERROR', 'reason': 'Alert configuration/data unavailable; inspect local setup', 'deliveries': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true', help='Local sample only; no messages sent')
    mode.add_argument('--test', action='store_true', help='Send a clearly labeled test to enabled destinations')
    args = parser.parse_args()
    message = {'id': 'test:' + uuid.uuid4().hex, 'text': 'TEST ALERT · AVAR4ISE\nOKX Demo notification test. This is not a trading signal and does not place an order.'}
    if args.preview:
        now = datetime.now(timezone.utc)
        packet = make_packet({'pair': 'BTC-USDT-SWAP', 'status': 'WATCHLIST',
                              'candidate': {'action': 'LONG', 'entry': '100000', 'stop_reference': '99500'}},
                             [{'id': 'example', 'source': 'Example source'}], [{'status': 'OK'}], now)
        proposal = {'packet_id': packet['input']['packet_id'], 'pair': 'BTC-USDT-SWAP',
                    'action': 'LONG', 'reason': 'Example AI rationale; not current market analysis.',
                    'invalidation': 'Example: price breaks through the stop area.', 'source_ids': ['example'],
                    'expires_at': packet['input']['expires_at']}
        message = signal_message(packet, proposal, now)
        card = message['card']
        text = 'SYNTHETIC EXAMPLE — NOT SENT\n\n'+card['title']+'\n'+card['subtitle']+'\n\n'
        text += '\n\n'.join(f["name"]+'\n'+f["value"] for f in card['fields'])
        text += f'\n\nPowered by [{BRAND_TEXT}]({BRAND_URL})'
        (RUNTIME_DIR / 'alerts-preview-discord.json').write_text(json.dumps(payload({'channel':'discord'},message),indent=2))
        (RUNTIME_DIR / 'alerts-preview-telegram.html').write_text(payload({'channel':'telegram','chat_id':'PREVIEW'},message)['text'])
        (RUNTIME_DIR / 'alerts-preview.txt').write_text(text + '\n')
        print(text)
        return 0
    try:
        targets = settings()
        if not targets:
            raise AlertError('Set ALERTS_ENABLED=true and fill local alert configuration')
        results = deliver(targets, message)
        print(json.dumps({'deliveries': results}, indent=2))
        return int(any(r['status'] != 'SENT' for r in results))
    except AlertError as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}))
        return 1
