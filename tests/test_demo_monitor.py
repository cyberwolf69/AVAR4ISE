import unittest
from unittest.mock import Mock
from decimal import Decimal
from hedging_tools.demo_monitor import monitor, position_limits
from hedging_tools.okx_demo import DemoError


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'pair': 'BTC-USDT-SWAP', 'direction': 'LONG', 'size': '.06', 'stop': '79600'}
        self.position = {'instId': 'BTC-USDT-SWAP', 'pos': '.06', 'avgPx': '80000',
                         'mgnMode': 'isolated', 'margin': '4.8', 'lever': '10', 'liqPx': '73000'}
        self.stop = {'algoClOrdId': 'tests', 'state': 'live', 'slTriggerPx': '79600',
                     'slOrdPx': '-1', 'slTriggerPxType': 'mark'}
        self.client = Mock()
        self.client.get.side_effect = lambda route, **kw: [self.position] if route == 'positions' else [self.stop]
        self.public = Mock()
        self.mark = {'instId': 'BTC-USDT-SWAP', 'ts': '1000000', 'markPx': '80000'}
        self.public.get.return_value = [self.mark]
        self.elapsed = 0
        self.record = Mock()

    def sleep(self, seconds):
        self.elapsed += seconds

    def run_monitor(self, seconds=10, **kwargs):
        return monitor(self.client, self.public, self.plan, 'test', Decimal('.01'), '1',
                       seconds, self.record, monotonic=lambda: self.elapsed,
                       wall=lambda: 1000 + self.elapsed, sleep=self.sleep, **kwargs)

    def test_time_limit_and_no_order_mutation(self):
        self.assertEqual(self.run_monitor(), 'TIME_LIMIT')
        self.assertEqual(self.elapsed, 10)
        self.assertEqual(self.record.call_count, 2)
        self.client.post.assert_not_called()

    def test_long_target_uses_actual_fill(self):
        self.mark['markPx'] = '80800'
        self.assertEqual(self.run_monitor(), 'TAKE_PROFIT_2R')

    def test_short_target(self):
        self.plan.update(direction='SHORT', stop='80400')
        self.position.update(pos='-.06', liqPx='87000')
        self.stop['slTriggerPx'] = '80400'
        self.mark['markPx'] = '79200'
        self.assertEqual(self.run_monitor(), 'TAKE_PROFIT_2R')

    def test_stop_threshold(self):
        self.mark['markPx'] = '79599'
        self.assertEqual(self.run_monitor(), 'STOP_THRESHOLD')

    def test_closed_on_exchange(self):
        self.client.get.return_value = []
        self.client.get.side_effect = None
        self.assertEqual(self.run_monitor(), 'POSITION_CLOSED_ON_EXCHANGE')
        self.public.get.assert_not_called()

    def test_missing_or_changed_stop_rejected(self):
        for key, value in [('state', 'canceled'), ('slTriggerPx', '79000'), ('slTriggerPxType', 'last')]:
            with self.subTest(key=key):
                original = self.stop[key]
                self.stop[key] = value
                with self.assertRaises(DemoError):
                    self.run_monitor()
                self.stop[key] = original

    def test_stale_mark_rejected(self):
        self.mark['ts'] = '960000'
        with self.assertRaises(DemoError):
            self.run_monitor()

    def test_margin_liquidation_and_direction_checks(self):
        for key, value in [('margin', '5.1'), ('liqPx', '79590'), ('pos', '-.06'), ('lever', '30')]:
            with self.subTest(key=key):
                original = self.position[key]
                self.position[key] = value
                with self.assertRaises(DemoError):
                    position_limits(self.position, self.plan, Decimal('.01'), '1')
                self.position[key] = original

    def test_actual_risk_budget_exceeded(self):
        with self.assertRaises(DemoError):
            position_limits(self.position, self.plan, Decimal('.01'), '.1')

    def test_adaptive_leverage_must_match_plan(self):
        self.plan['leverage'] = '20'
        self.position['lever'] = '20'
        position_limits(self.position, self.plan, Decimal('.01'), '1')
        self.position['lever'] = '30'
        with self.assertRaises(DemoError):
            position_limits(self.position, self.plan, Decimal('.01'), '1')

    def test_expired_signal_closes_without_waiting(self):
        self.assertEqual(self.run_monitor(expires_at=999), 'SIGNAL_EXPIRED')
        self.client.get.assert_not_called()

    def test_stop_request_returns_to_cleanup(self):
        self.assertEqual(self.run_monitor(stop_requested=lambda: True), 'STOP_REQUESTED')
        self.client.get.assert_not_called()

    def test_invalid_duration_before_network(self):
        for seconds in (0, 301, -1):
            with self.assertRaises(ValueError):
                self.run_monitor(seconds)
        self.client.get.assert_not_called()

    def test_network_error_propagates_to_executor(self):
        self.public.get.side_effect = DemoError('Network unavailable')
        with self.assertRaises(DemoError):
            self.run_monitor()
