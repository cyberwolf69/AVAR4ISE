import copy
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock
from hedging_tools.analyst import make_packet
from hedging_tools.signal_history import SignalHistory, evaluate, MINUTE, DAY
from hedging_tools.history_worker import HistoryWorker

NOW = datetime(2026, 10, 18, 10, 0, 30, tzinfo=timezone.utc)


def candle(ts, low='99.5', high='100.5', o='100', close='100', confirm='1'):
    return [str(ts), o, high, low, close, confirm]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = SignalHistory(Path(self.tmp.name)/'history.sqlite3')
        self.packet = make_packet({'pair': 'BTC-USDT-SWAP', 'status': 'WATCHLIST',
            'candidate': {'action': 'LONG', 'entry': '100', 'stop_reference': '99'}},
            [{'id': 'n', 'source': 'Test'}], [{'status': 'OK'}], NOW)
        self.proposal = {'packet_id': self.packet['input']['packet_id'], 'pair': 'BTC-USDT-SWAP',
            'action': 'LONG', 'reason': 'Test', 'invalidation': 'Test', 'source_ids': ['n'],
            'expires_at': self.packet['input']['expires_at']}
        self.history.register(self.packet, self.proposal, NOW)
        self.state = self.history.records()[0]
        self.start = self.state['next_ms']

    def apply(self, rows, state=None):
        return evaluate(state or self.state, rows, self.start+2000*MINUTE)

    def activated(self):
        return self.apply([candle(self.start)])

    def test_registration_deduplicates_first_reference_and_survives_restart(self):
        self.history.register(self.packet, self.proposal, NOW+timedelta(seconds=5))
        restored = SignalHistory(self.history.path)
        self.assertEqual(len(restored.records()), 1)
        self.assertEqual(restored.records()[0]['issued_ms'], int(NOW.timestamp()*1000))
        self.assertEqual(restored.summary()['scope'], 'REFERENCE_SIGNALS_NOT_TRADE_PNL')

    def test_pre_signal_straddling_and_unclosed_candles_do_not_count(self):
        rows = [candle(self.start-MINUTE, low='90', high='110'), candle(self.start, low='90', high='110', confirm='0')]
        result = self.apply(rows)
        self.assertEqual(result['status'], 'PENDING')
        self.assertEqual(result['next_ms'], self.start)
        self.assertEqual(result['events'], [])

    def test_clean_entry_tp1_tp2_tp3_path(self):
        rows = [candle(self.start), candle(self.start+MINUTE, '100.3', '101.1', '100.5', '101'),
                candle(self.start+2*MINUTE, '101.2', '102.1', '101.5', '102'),
                candle(self.start+3*MINUTE, '102.2', '103.1', '102.5', '103')]
        result = self.apply(rows)
        self.assertEqual(result['status'], 'TP3')
        self.assertEqual([e['event'] for e in result['events']], ['ENTRY_REFERENCE_TOUCHED', 'TP1', 'TP2', 'TP3'])
        self.assertEqual(result['actual_trade_pnl'], 'NOT_COMPUTED')

    def test_initial_stop_and_protected_stop_distinguished(self):
        s = self.activated()
        result = self.apply([candle(self.start+MINUTE, '98.9', '100.2')], s)
        self.assertEqual(result['status'], 'SL')
        s = self.apply([candle(self.start+MINUTE, '100.3', '101.1', '100.5', '101')], s)
        self.assertEqual(s['stage'], 1)
        result = self.apply([candle(self.start+2*MINUTE, '100.1', '101', '100.5', '100.4')], s)
        self.assertEqual(result['status'], 'PROTECTED_STOP')

    def test_entry_and_target_same_bar_is_ambiguous(self):
        result = self.apply([candle(self.start, '99.5', '101.5')])
        self.assertEqual(result['status'], 'AMBIGUOUS')
        self.assertEqual(result['stage'], 0)

    def test_stop_and_target_same_bar_never_counted_as_win(self):
        result = self.apply([candle(self.start+MINUTE, '98', '103.5')], self.activated())
        self.assertEqual(result['status'], 'AMBIGUOUS')
        self.assertNotIn('TP3', [e['event'] for e in result['events']])

    def test_new_bep_and_tp_same_bar_is_ambiguous_even_if_tp3_reached(self):
        result = self.apply([candle(self.start+MINUTE, '100.1', '103.5', '100.5', '103')], self.activated())
        self.assertEqual(result['status'], 'AMBIGUOUS')
        self.assertEqual(result['stage'], 0)

    def test_expiry_requires_contiguous_observations(self):
        rows = [candle(self.start+i*MINUTE, '100.2', '100.8', '100.5', '100.6') for i in range(15)]
        result = self.apply(rows)
        self.assertEqual(result['status'], 'EXPIRED')
        gap = self.apply(rows[1:])
        self.assertEqual(gap['status'], 'PENDING')
        self.assertEqual(gap['quality'], 'DATA_GAP')
        self.assertEqual(gap['next_ms'], self.start)

    def test_touch_in_partial_expiry_bar_is_ambiguous(self):
        state = copy.deepcopy(self.state)
        state['expires_ms'] = self.start+MINUTE//2
        self.assertEqual(self.apply([candle(self.start)], state)['status'], 'AMBIGUOUS')

    def test_outcome_timeout_does_not_reuse_entry_expiry(self):
        state = self.activated()
        rows = [candle(self.start+i*MINUTE) for i in range(1, 1441)]
        result = self.apply(rows, state)
        self.assertEqual(result['status'], 'TIMEOUT')
        self.assertEqual(result['events'][-1]['event'], 'TIMEOUT_24H')

    def test_short_levels_reverse_correctly(self):
        state = dict(self.state, direction='SHORT', stop='101', current_stop='101', targets=['99', '98', '97'])
        rows = [candle(self.start), candle(self.start+MINUTE, '98.9', '99.7', '99.5', '99'),
                candle(self.start+2*MINUTE, '97.9', '98.8', '98.5', '98'),
                candle(self.start+3*MINUTE, '96.9', '97.8', '97.5', '97')]
        self.assertEqual(self.apply(rows, state)['status'], 'TP3')

    def test_updater_fetches_forward_and_never_counts_same_candle_twice(self):
        client = Mock()
        client.get.return_value = [candle(self.start)]
        now = datetime.fromtimestamp((self.start+MINUTE)/1000, timezone.utc)
        self.history.update(client, now)
        self.history.update(client, now)
        self.assertEqual(len(self.history.records()[0]['events']), 1)
        client.get.assert_called_once_with('mark_history', instId='BTC-USDT-SWAP', bar='1m', after=str(self.start+MINUTE), limit='100')

    def test_failed_pair_cannot_starve_other_pairs(self):
        packet = copy.deepcopy(self.packet)
        packet['input']['pair'] = 'ETH-USDT-SWAP'
        packet['input']['market']['pair'] = 'ETH-USDT-SWAP'
        packet['input']['packet_id'] = 'eth-packet'
        proposal = dict(self.proposal, pair='ETH-USDT-SWAP', packet_id='eth-packet')
        self.history.register(packet, proposal, NOW)
        client = Mock(get=Mock(side_effect=OSError()))
        now = NOW+timedelta(minutes=2)
        self.history.update(client, now, max_pairs=1)
        first = client.get.call_args.kwargs['instId']
        self.history.update(client, now, max_pairs=1)
        second = client.get.call_args.kwargs['instId']
        self.assertNotEqual(first, second)

    def test_network_failure_retains_cursor_and_history(self):
        result = self.history.update(Mock(get=Mock(side_effect=OSError())), NOW+timedelta(minutes=2))
        self.assertEqual(result['data_incomplete'], 1)
        self.assertEqual(self.history.records()[0]['next_ms'], self.start)
        self.assertEqual(self.history.records()[0]['status'], 'PENDING')

    def test_slow_concurrent_updater_cannot_overwrite_progress(self):
        newer = self.activated()
        self.history.commit(self.state, newer)
        self.history.commit(self.state, dict(self.state, quality='DATA_ERROR'))
        self.assertEqual(self.history.records()[0]['status'], 'ACTIVE')

    def test_expired_proposal_cannot_be_backfilled_as_new_signal(self):
        with self.assertRaises(ValueError):
            self.history.register(self.packet, self.proposal, NOW+timedelta(hours=1))

    def test_wait_has_no_tracking_record(self):
        proposal = dict(self.proposal, action='WAIT')
        self.assertIsNone(self.history.register(self.packet, proposal, NOW))
        self.assertEqual(self.history.summary()['total'], 1)

    def test_conflicting_duplicate_candle_rejected(self):
        with self.assertRaises(ValueError):
            self.apply([candle(self.start), candle(self.start, high='100.8')])


class WorkerTests(unittest.TestCase):
    def test_worker_runs_independently_and_times_out_without_blocking(self):
        clock, launch = Mock(return_value=0), Mock()
        child = launch.return_value
        child.poll.return_value = None
        worker = HistoryWorker(clock=clock, launch=launch)
        worker.tick()
        launch.assert_called_once()
        clock.return_value = 121
        worker.tick()
        child.kill.assert_called_once()
        child.wait.assert_not_called()
        self.assertIn('hedging_tools.signal_history', launch.call_args.args[0])

    def test_failed_launch_is_delayed(self):
        launch = Mock(side_effect=OSError())
        worker = HistoryWorker(clock=lambda: 0, launch=launch)
        worker.tick()
        worker.tick()
        launch.assert_called_once()
