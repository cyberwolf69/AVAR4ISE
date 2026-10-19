import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from hedging_tools.demo_runner import RunLog, run_loop


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'runs.sqlite3'
        self.log = RunLog(self.path)
        self.stop = Mock()
        self.stop.is_set.return_value = False
        self.stop.wait.return_value = False
        self.cycle = Mock(return_value={'status': 'WAIT', 'orders_sent': 0})

    def run_loop(self, cycles=3):
        return run_loop(cycles, 60, 60, self.log, self.stop, self.cycle)

    def test_wait_repeats_and_persists_each_cycle(self):
        report = self.run_loop()
        self.assertEqual(report['status'], 'CYCLE_LIMIT_REACHED')
        self.assertEqual(self.cycle.call_count, 3)
        self.assertEqual(self.stop.wait.call_count, 2)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute('SELECT finished_at,report FROM runner_cycles').fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(t and json.loads(r)['status'] == 'WAIT' for t, r in rows))

    def test_stops_after_first_trade(self):
        self.cycle.return_value = {'status': 'COMPLETE', 'settlement_status': 'VERIFIED'}
        self.assertEqual(self.run_loop()['status'], 'TRADE_LIMIT_REACHED')
        self.cycle.assert_called_once()
        self.stop.wait.assert_not_called()

    def test_errors_and_uncertain_wait_never_repeat(self):
        for report in ({'status': 'RESEARCH_FAILED'}, {'status': 'CLOSED_WITH_TEST_ERROR'}, {'status': 'WAIT'}, {'status': 'WAIT', 'orders_sent': 1}):
            with self.subTest(report=report):
                self.cycle.reset_mock()
                self.cycle.return_value = report
                self.assertEqual(self.run_loop()['status'], 'BLOCKED')
                self.cycle.assert_called_once()

    def test_daily_halt_ends_run(self):
        self.cycle.return_value = {'status': 'DAILY_HALT', 'orders_sent': 0}
        self.assertEqual(self.run_loop()['status'], 'DAILY_HALT')
        self.cycle.assert_called_once()

    def test_stop_during_interval(self):
        self.stop.wait.return_value = True
        self.assertEqual(self.run_loop()['status'], 'STOPPED')
        self.cycle.assert_called_once()

    def test_stop_before_cycle(self):
        self.stop.is_set.return_value = True
        self.assertEqual(self.run_loop()['status'], 'STOPPED')
        self.cycle.assert_not_called()

    def test_unexpected_error_is_sanitized_and_logged(self):
        self.cycle.side_effect = RuntimeError('sensitive error text')
        report = self.run_loop()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['last_cycle']['reason'], 'RuntimeError')
        self.cycle.assert_called_once()

    def test_invalid_configuration_never_calls_cycle(self):
        for cycles, interval, holding in [(0, 60, 60), (21, 60, 60), (1, 1, 60), (1, 60, 301)]:
            with self.assertRaises(ValueError):
                run_loop(cycles, interval, holding, self.log, self.stop, self.cycle)
        self.cycle.assert_not_called()

    def test_multiple_verified_trades_wait_then_stop_at_cap(self):
        self.cycle.return_value = {'status': 'COMPLETE', 'settlement_status': 'VERIFIED'}
        result = run_loop(5, 60, 60, self.log, self.stop, self.cycle, max_trades=2)
        self.assertEqual(result['status'], 'TRADE_LIMIT_REACHED')
        self.assertEqual(result['verified_trades'], 2)
        self.assertEqual(self.cycle.call_count, 2)
        self.stop.wait.assert_called_once_with(900)

    def test_unverified_complete_never_continues(self):
        self.cycle.return_value = {'status': 'COMPLETE'}
        result = run_loop(5, 60, 60, self.log, self.stop, self.cycle, max_trades=3)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['verified_trades'], 0)
        self.cycle.assert_called_once()

    def test_trade_crossing_daily_limit_stops_immediately(self):
        self.cycle.return_value = {'status': 'COMPLETE', 'settlement_status': 'VERIFIED', 'budget': {'halted': True}}
        result = run_loop(5, 60, 60, self.log, self.stop, self.cycle, max_trades=3)
        self.assertEqual(result['status'], 'DAILY_HALT')
        self.stop.wait.assert_not_called()

    def test_cycle_limit_still_applies_after_trade(self):
        self.cycle.return_value = {'status': 'COMPLETE', 'settlement_status': 'VERIFIED'}
        result = run_loop(1, 60, 60, self.log, self.stop, self.cycle, max_trades=3)
        self.assertEqual(result['status'], 'CYCLE_LIMIT_REACHED')
        self.stop.wait.assert_not_called()

    def test_cooldown_does_not_count_as_trade(self):
        self.cycle.return_value = {'status': 'COOLDOWN', 'orders_sent': 0}
        result = self.run_loop()
        self.assertEqual(result['verified_trades'], 0)
        self.assertEqual(result['status'], 'CYCLE_LIMIT_REACHED')
