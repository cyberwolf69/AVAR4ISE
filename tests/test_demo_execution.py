import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from hedging_tools.demo_execution import plan, accepted, run
from hedging_tools.okx_demo import OKXDemo, KEYS, DemoError


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.instrument = {"instId": "BTC-USDT-SWAP", "ctType": "linear", "settleCcy": "USDT",
                           "ctValCcy": "BTC", "state": "live", "ctVal": "0.01", "ctMult": "1",
                           "tickSz": "0.1", "lotSz": "0.01", "minSz": "0.01", "lever": "100"}
        self.ticker = {"instId": "BTC-USDT-SWAP", "bidPx": "80000", "askPx": "80000.1"}

    def test_sizing_and_stop(self):
        result = plan(self.instrument, self.ticker)
        self.assertLessEqual(float(result["margin_upper_bound"]), 5)
        self.assertLessEqual(float(result["planned_risk"]), 1)
        self.assertLess(float(result["stop"]), 80000)
        self.assertEqual(result["leverage"], "10")

    def test_minimum_size_blocks(self):
        self.instrument["minSz"] = "1"
        with self.assertRaises(ValueError):
            plan(self.instrument, self.ticker)

    def test_short_stop_and_margin(self):
        result = plan(self.instrument, self.ticker, {"pair": "BTC-USDT-SWAP", "action": "SHORT", "stop": "80400.01"})
        self.assertEqual(result["direction"], "SHORT")
        self.assertEqual(result["stop"], "80400.1")
        self.assertLessEqual(float(result["margin_upper_bound"]), 5)

    def test_short_wrong_stop_is_rejected(self):
        with self.assertRaises(ValueError):
            plan(self.instrument, self.ticker, {"pair": "BTC-USDT-SWAP", "action": "SHORT", "stop": "79000"})

    def test_adaptive_leverage_tight_and_wide_stops(self):
        signal = {'pair': 'BTC-USDT-SWAP', 'action': 'LONG', 'stop': '79920'}
        tight = plan(self.instrument, self.ticker, signal)
        wider = plan(self.instrument, self.ticker, {**signal, 'stop': '79000'})
        self.assertEqual(tight['leverage'], '30')
        self.assertLess(int(wider['leverage']), int(tight['leverage']))
        self.assertGreaterEqual(int(wider['leverage']), 10)

    def test_exchange_leverage_cap(self):
        self.instrument['lever'] = '17'
        result = plan(self.instrument, self.ticker, {'pair': 'BTC-USDT-SWAP', 'action': 'LONG', 'stop': '79920'})
        self.assertEqual(result['leverage'], '17')

    def test_adaptive_no_fit(self):
        with self.assertRaises(ValueError):
            plan(self.instrument, self.ticker, {'pair': 'BTC-USDT-SWAP', 'action': 'LONG', 'stop': '70000'})

    def test_adaptive_risk_margin_grid(self):
        from decimal import Decimal
        accepted_count = 0
        for direction, sign in [('LONG', -1), ('SHORT', 1)]:
            for distance in (80, 200, 400, 800, 1200):
                for budget in ('.25', '.5', '1'):
                    try:
                        result = plan(self.instrument, self.ticker,
                                      {'pair': 'BTC-USDT-SWAP', 'action': direction, 'stop': str(80000 + sign * distance)}, budget)
                    except ValueError:
                        continue
                    accepted_count += 1
                    self.assertLessEqual(Decimal(result['planned_risk']), Decimal(budget))
                    self.assertLessEqual(Decimal(result['margin_upper_bound']), 5)
                    self.assertTrue(10 <= int(result['leverage']) <= 30)
                    self.assertEqual(Decimal(result['size']) % Decimal(self.instrument['lotSz']), 0)
        self.assertGreater(accepted_count, 10)

    def test_remaining_daily_risk_blocks_size(self):
        with self.assertRaises(ValueError):
            plan(self.instrument, self.ticker, risk_budget="0.01")

    def test_wide_spread_blocks(self):
        self.ticker["askPx"] = "81000"
        with self.assertRaises(ValueError):
            plan(self.instrument, self.ticker)

    def test_subcode_rejection(self):
        with self.assertRaises(DemoError):
            accepted([{"sCode": "51000"}])

    def test_post_has_demo_header_and_signed_body(self):
        opener = Mock(return_value=io.BytesIO(b'{"code":"0","data":[{"sCode":"0"}]}'))
        client = OKXDemo(dict(zip(KEYS, ("a", "b", "c"))), opener)
        client.post("order", {"instId": "BTC-USDT-SWAP", "reduceOnly": True})
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(json.loads(request.data)["reduceOnly"])
        self.assertEqual(dict((k.lower(), v) for k,v in request.header_items())["x-simulated-trading"], "1")

    def test_monitor_failure_still_closes_reduce_only(self):
        client, public = Mock(), Mock()
        state = {'open': False, 'prefix': None}
        def get(route, **params):
            if route == 'config':
                return [{'posMode': 'net_mode'}]
            if route == 'balance':
                return [{'details': [{'ccy': 'USDT', 'availBal': '50'}]}]
            if route == 'instruments':
                return [self.instrument]
            if route == 'order':
                return [{'state': 'filled', 'accFillSz': '.06', 'avgPx': '80000'}]
            if route == 'positions' and state['open']:
                return [{'instId': 'BTC-USDT-SWAP', 'pos': '.06', 'mgnMode': 'isolated'}]
            if route == 'algos' and state['open']:
                return [{'algoClOrdId': state['prefix'] + 's'}]
            return []
        def post(route, body):
            if route == 'order':
                if body.get('reduceOnly'):
                    state['open'] = False
                else:
                    state.update(open=True, prefix=body['clOrdId'])
            return [{'sCode': '0'}]
        client.get.side_effect, client.post.side_effect = get, post
        public.get.return_value = [{**self.ticker, 'ts': '1000000'}]
        with tempfile.TemporaryDirectory() as directory, patch('hedging_tools.demo_execution.time.time', return_value=1000), patch('hedging_tools.demo_execution.monitor', side_effect=DemoError('Missing stop')) as watch:
            journal = Path(directory) / 'test.json'
            result = run(client, public, journal, hold_seconds=60)
            self.assertEqual(result['status'], 'CLOSED_WITH_TEST_ERROR')
            self.assertEqual(result['final_open_positions'], 0)
            self.assertEqual(json.loads(journal.read_text())['error'], 'Missing stop')
            watch.assert_called_once()
        orders = [call.args[1] for call in client.post.call_args_list if call.args[0] == 'order']
        self.assertEqual(len(orders), 2)
        self.assertTrue(orders[-1]['reduceOnly'])
        self.assertEqual(orders[-1]['side'], 'sell')
