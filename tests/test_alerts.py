import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta
from hedging_tools.alerts import settings, AlertError, NoRedirect, signal_message, payload, send, deliver, notify_signals
from hedging_tools.analyst import make_packet


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.discord = {'channel': 'discord', 'url': 'https://discord.com/api/webhooks/123/testtoken?wait=true'}
        self.telegram = {'channel': 'telegram', 'url': 'https://api.telegram.org/bot123:testtoken/sendMessage', 'chat_id': '@testchannel'}
        self.now = datetime.now(timezone.utc)
        self.packet = make_packet({'pair': 'BTC-USDT-SWAP', 'status': 'WATCHLIST',
            'candidate': {'action': 'LONG', 'entry': '100', 'stop_reference': '99'}},
            [{'id': 'source1', 'source': 'Example feed'}], [{'status': 'OK'}], self.now)
        self.proposal = {'packet_id': self.packet['input']['packet_id'], 'pair': 'BTC-USDT-SWAP',
            'action': 'LONG', 'reason': 'Trend aligned @everyone', 'invalidation': 'Below support',
            'source_ids': ['source1'], 'expires_at': self.packet['input']['expires_at']}

    def test_disabled_without_credentials(self):
        self.assertEqual(settings(self.root / '.env'), [])

    def test_both_destinations_configured(self):
        path = self.root / '.env'
        path.write_text('ALERTS_ENABLED=true\nALERT_CHANNELS=discord,telegram\nDISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/abc\nTELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=@example\n')
        self.assertEqual([t['channel'] for t in settings(path)], ['discord', 'telegram'])

    def test_untrusted_webhook_host_rejected(self):
        path = self.root / '.env'
        path.write_text('ALERTS_ENABLED=true\nALERT_CHANNELS=discord\nDISCORD_WEBHOOK_URL=https://example.com/api/webhooks/123/secret\n')
        with self.assertRaises(AlertError) as caught:
            settings(path)
        self.assertNotIn('secret', str(caught.exception))

    def test_signal_is_reference_not_order_and_no_mentions(self):
        message = signal_message(self.packet, self.proposal, self.now)
        self.assertNotIn('BELUM ORDER', message['text'])
        self.assertNotIn('signal only', message['text'].lower())
        self.assertIn('TP1: 101', message['text'])
        self.assertIn('TP3: 103', message['text'])
        self.assertIn('TP2: 102', message['text'])
        self.assertNotIn('@everyone', message['text'])
        self.assertEqual(payload(self.discord, message)['allowed_mentions'], {'parse': []})
        self.assertEqual(payload(self.telegram, message)['parse_mode'], 'HTML')
        self.assertIn('Powered by [𝕏 web3rai](https://www.x.com/web3rai)', payload(self.discord, message)['embeds'][0]['fields'][-1]['value'])
        self.assertIn('<a href="https://www.x.com/web3rai">𝕏 web3rai</a>', payload(self.telegram, message)['text'])

    def test_untrusted_html_is_escaped_before_branded_link(self):
        value = payload(self.telegram, {'text': '<a href="evil">bad</a> & text'})['text']
        self.assertIn('&lt;a href=', value)
        self.assertEqual(value.count('<a href='), 1)

    def test_wait_and_expired_never_alert(self):
        self.proposal['action'] = 'WAIT'
        self.assertIsNone(signal_message(self.packet, self.proposal, self.now))
        self.proposal['action'] = 'LONG'
        with self.assertRaises(ValueError):
            signal_message(self.packet, self.proposal, self.now + timedelta(hours=1))

    def test_success_requires_provider_confirmation(self):
        send(self.discord, {'text': 'Test'}, Mock(return_value=io.BytesIO(b'{"id":"42"}')))
        send(self.telegram, {'text': 'Test'}, Mock(return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":42}}')))
        with self.assertRaises(AlertError):
            send(self.telegram, {'text': 'Test'}, Mock(return_value=io.BytesIO(b'{"ok":false}')))

    def test_exception_does_not_expose_token(self):
        with self.assertRaises(AlertError) as caught:
            send(self.telegram, {'text': 'Test'}, Mock(side_effect=OSError(self.telegram['url'])))
        self.assertNotIn('testtoken', str(caught.exception))

    def test_redirect_refused(self):
        with self.assertRaises(AlertError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com')

    def test_dedup_survives_reopening_database_and_is_per_destination(self):
        db, sender = self.root / 'alerts.sqlite3', Mock()
        message = {'id': 'signal1', 'text': 'Test'}
        first = deliver([self.discord, self.telegram], message, db, sender)
        second = deliver([self.discord, self.telegram], message, db, sender)
        self.assertTrue(all(r['status'] == 'SENT' for r in first))
        self.assertTrue(all(r['status'] == 'DUPLICATE_SUPPRESSED' for r in second))
        self.assertEqual(sender.call_count, 2)
        self.assertNotIn(b'testtoken', db.read_bytes())

    def test_uncertain_delivery_not_automatically_retried(self):
        sender, db = Mock(side_effect=TimeoutError()), self.root / 'alerts.sqlite3'
        message = {'id': 'signal1', 'text': 'Test'}
        self.assertEqual(deliver([self.discord], message, db, sender)[0]['status'], 'UNCONFIRMED')
        self.assertEqual(deliver([self.discord], message, db, sender)[0]['status'], 'DUPLICATE_SUPPRESSED')
        sender.assert_called_once()

    def test_expired_delivery_never_sends(self):
        sender = Mock()
        result = deliver([self.discord], {'id': 'old', 'text': 'Old', 'expires_at': (self.now-timedelta(seconds=1)).isoformat()}, self.root / 'alerts.sqlite3', sender)
        self.assertEqual(result[0]['status'], 'EXPIRED')
        sender.assert_not_called()

    def test_integrated_notifications_only_validated_signals(self):
        with patch('hedging_tools.alerts.settings', return_value=[self.discord]), patch('hedging_tools.alerts.deliver', return_value=[{'status': 'SENT'}]) as delivery:
            result = notify_signals([self.packet], [{'status': 'VALIDATED', 'proposal': self.proposal}])
            self.assertEqual(result['deliveries'], [{'status': 'SENT'}])
            delivery.assert_called_once()
            delivery.reset_mock()
            notify_signals([self.packet], [{'status': 'SKIPPED'}])
            delivery.assert_not_called()

    def test_short_card_prices_and_moves(self):
        self.packet['input']['market']['candidate'].update(action='SHORT', stop_reference='101')
        self.proposal['action']='SHORT'
        message=signal_message(self.packet,self.proposal,self.now)
        fields=payload(self.discord,message)['embeds'][0]['fields']
        self.assertIn('SHORT',message['card']['title'])
        self.assertIn('`99`',fields[2]['value'])
        self.assertIn('-1.00%',fields[2]['value'])
        self.assertEqual([f['inline'] for f in fields[:5]],[True]*5)

    def test_card_escapes_untrusted_text_and_stays_in_limits(self):
        self.proposal['reason']='Bad <b>tag</b> @everyone [link](https://evil.example) '+('word '*350)
        message=signal_message(self.packet,self.proposal,self.now)
        d=payload(self.discord,message)['embeds'][0]
        t=payload(self.telegram,message)['text']
        self.assertLess(len(t),4096)
        self.assertTrue(all(len(f['value'])<=1024 for f in d['fields']))
        self.assertNotIn('@everyone',str(d))
        self.assertNotIn('<b>tag</b>',t)
        self.assertEqual(t.count('<a href='),1)
        self.assertTrue(message['card']['fields'][5]['value'].endswith('…'))
