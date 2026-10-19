import io
import json
import tempfile
import sqlite3
from contextlib import closing
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from hedging_tools import research
from hedging_tools.ai_client import AIError


class ResearchIntegrationTests(unittest.TestCase):
    def execute(self, failure_first=False, scan_error=False, no_news=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc)
            pairs = ['TOKEN'+str(i)+'-USDT-SWAP' for i in range(5)]
            results = [{'pair': p, 'observed_at': now.isoformat(), 'status': 'WATCHLIST',
                        'candidate': {'action': 'LONG', 'entry': '100', 'stop_reference': '99'}} for p in pairs]
            if scan_error:
                results[-1] = {'pair': pairs[-1], 'status': 'DATA_ERROR'}
            calls = []
            def analyze(envelope):
                calls.append(envelope['input']['pair'])
                if failure_first and len(calls) == 1:
                    raise AIError('Provider unavailable')
                data = envelope['input']
                return {'proposal': {'packet_id': data['packet_id'], 'pair': data['pair'], 'action': 'LONG',
                        'reason': 'Test', 'invalidation': 'Test', 'source_ids': ['n'], 'expires_at': data['expires_at']}}
            ai = Mock(analyze=Mock(side_effect=analyze))
            history = Mock()
            with patch.object(research, 'ROOT', root), patch.object(research, 'AIClient', return_value=ai), \
                 patch.object(research, 'SignalHistory', return_value=history), \
                 patch.object(research, 'OKXPublic'), \
                 patch.object(research, 'select_universe', return_value={'status': 'READY', 'pairs': pairs}), \
                 patch.object(research, 'fetch_news', return_value=[{'status': 'ERROR' if no_news else 'OK'}]), \
                 patch.object(research, 'news_for_pair', return_value=[] if no_news else [{'id': 'n', 'source': 'Test'}]), \
                 patch.object(research, 'run', return_value={'run_id': 'test', 'results': results}) as scan, \
                 patch.object(research, 'notify_signals', return_value={'status': 'DISABLED', 'deliveries': []}) as notify, \
                 patch('sys.argv', ['research', '--portfolio', '--ai', '--db', str(root/'scans.sqlite3'), '--output', str(root/'output.json')]), \
                 patch('sys.stdout', new_callable=io.StringIO):
                code = research.main()
                output = json.loads((root/'output.json').read_text())
                scan.assert_called_once()
                self.assertEqual(scan.call_args.kwargs['workers'], 4)
                self.assertEqual(scan.call_args.args[1]['pairs'], pairs)
                return code, output, history.register.call_count, notify.call_count

    def test_review_priority_rotates_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'scans.sqlite3'
            packets = [{'input': {'pair': p}} for p in ('BTC', 'ETH', 'SOL', 'ADA')]
            self.assertEqual(research.review_priority(packets, db), [0, 1, 2, 3])
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executemany('INSERT INTO ai_review_cursor VALUES (?,?)',
                                 [('BTC', '2026-10-18T10:00:00+00:00'), ('ETH', '2026-10-18T10:01:00+00:00')])
            self.assertEqual(research.review_priority(packets, db), [2, 3, 0, 1])

    def test_three_ai_calls_and_all_validated_signals_tracked_even_without_alert_delivery(self):
        code, output, tracked, notified = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(output['ai_calls'], 3)
        self.assertEqual((tracked, notified), (3, 3))
        self.assertEqual(output['analyses'][-1]['reason'], 'AI_CALL_BUDGET_REACHED')

    def test_one_provider_error_does_not_discard_other_valid_signals(self):
        code, output, tracked, notified = self.execute(failure_first=True)
        self.assertEqual(code, 0)
        self.assertEqual(output['analyses'][0]['status'], 'ERROR')
        self.assertEqual((tracked, notified), (2, 2))

    def test_one_bad_market_does_not_discard_valid_pairs(self):
        code, output, tracked, notified = self.execute(scan_error=True)
        self.assertEqual(code, 0)
        self.assertEqual(tracked, 3)

    def test_news_outage_does_not_block_technical_review(self):
        code, output, tracked, notified = self.execute(no_news=True)
        self.assertEqual(code, 0)
        self.assertEqual(output['ai_calls'], 3)
        self.assertEqual(tracked, 3)
        self.assertEqual(output['packets'][0]['input']['news'], [])
        self.assertEqual(output['packets'][0]['input']['feed_health'][0]['status'], 'ERROR')
