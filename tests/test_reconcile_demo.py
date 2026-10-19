import unittest
from unittest.mock import Mock
from hedging_tools.reconcile_demo import reconcile
from hedging_tools.okx_demo import DemoError


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.report = {"mode": "OKX_DEMO", "purpose": "EXECUTION_TEST_NOT_AI_SIGNAL",
                       "plan": {"pair": "BTC-USDT-SWAP"}, "client_id": "test"}
        self.orders = [{"ordId": str(i), "clOrdId": "test" + suffix, "instId": "BTC-USDT-SWAP",
                        "side": side, "state": "filled", "avgPx": "80000", "accFillSz": "0.06",
                        "fee": "-0.024", "feeCcy": "USDT", "pnl": "0"}
                       for i, (suffix, side) in enumerate((("", "buy"), ("x", "sell")))]
        self.positions = []
        self.client = Mock()
        self.client.get.side_effect = self.get

    def get(self, route, **params):
        if route == "order":
            return [self.orders[params["clOrdId"].endswith("x")]]
        if route == "positions":
            return self.positions
        if route == "balance":
            return [{"details": [{"ccy": "USDT", "eq": "50", "availBal": "50"}]}]
        return []

    def test_flat_fees_and_no_mutation(self):
        result = reconcile(self.client, self.report)
        self.assertEqual(result["status"], "VERIFIED_FLAT")
        self.assertEqual(result["net_trade_pnl_excluding_funding_usdt"], "-0.048")
        self.client.post.assert_not_called()

    def test_remaining_position_is_not_complete(self):
        self.positions = [{"pos": "0.01"}]
        self.assertEqual(reconcile(self.client, self.report)["status"], "NEEDS_REVIEW")

    def test_ai_short_reconciliation(self):
        self.report['purpose'] = 'AI_SIGNAL_EXECUTION_TEST'
        self.report['plan']['direction'] = 'SHORT'
        self.orders[0]['side'], self.orders[1]['side'] = 'sell', 'buy'
        self.assertEqual(reconcile(self.client, self.report)['status'], 'VERIFIED_FLAT')

    def test_mismatched_exit_size_is_not_complete(self):
        self.orders[1]["accFillSz"] = "0.05"
        with self.assertRaises(DemoError):
            reconcile(self.client, self.report)

    def test_terminal_partial_entry_can_reconcile(self):
        self.orders[0]['state'] = 'canceled'
        self.orders[0]['accFillSz'] = self.orders[1]['accFillSz'] = '0.02'
        self.assertEqual(reconcile(self.client, self.report)['status'], 'VERIFIED_FLAT')

    def test_nonterminal_partial_entry_is_not_settled(self):
        self.orders[0]['state'] = 'partially_filled'
        self.assertEqual(reconcile(self.client, self.report)['status'], 'NEEDS_REVIEW')

    def test_zero_fills_cannot_be_counted_as_trade(self):
        for order in self.orders:
            order.update(state='canceled', accFillSz='0')
        self.assertEqual(reconcile(self.client, self.report)['status'], 'NEEDS_REVIEW')

    def test_wrong_order_identity_rejected(self):
        self.orders[1]["clOrdId"] = "other"
        with self.assertRaises(DemoError):
            reconcile(self.client, self.report)

    def test_unknown_fee_not_reported_as_zero(self):
        self.orders[1]["feeCcy"] = "BTC"
        result = reconcile(self.client, self.report)
        self.assertIn("pnl_status", result)
        self.assertNotIn("net_trade_pnl_excluding_funding_usdt", result)


class NativeStopTests(unittest.TestCase):
    setUp = ReconcileTests.setUp
    get = ReconcileTests.get
    def native_fixture(self, combined=False):
        self.report['plan'].update(stop='79600', direction='LONG')
        self.algo = {'algoId': '900', 'algoClOrdId': 'tests', 'instId': 'BTC-USDT-SWAP',
                     'state': 'effective', 'ordType': 'conditional', 'side': 'sell',
                     'slOrdPx': '-1', 'slTriggerPxType': 'mark', 'slTriggerPx': '79600',
                     'ordIdList': ['901']}
        self.child = {**self.orders[1], 'ordId': '901', 'clOrdId': '', 'algoId': '900',
                      'algoClOrdId': 'tests', 'pnl': '-.24', 'accFillSz': '.04' if combined else '.06'}
        if combined:
            self.orders[1]['accFillSz'] = '.02'
        def get(route, **params):
            if route == 'algo':
                return [self.algo]
            if route == 'order' and 'ordId' in params:
                return [self.child]
            if route == 'order' and params.get('clOrdId') == 'testx' and not combined:
                raise DemoError('Missing order', api_code='51603')
            return self.get(route, **params)
        self.client.get.side_effect = get

    def test_native_stop_fill_and_fee_reconciled(self):
        self.native_fixture()
        result = reconcile(self.client, self.report)
        self.assertEqual(result['status'], 'VERIFIED_FLAT')
        self.assertEqual(result['exit_source'], 'NATIVE_STOP')
        self.assertEqual(result['net_trade_pnl_excluding_funding_usdt'], '-0.288')
        self.client.post.assert_not_called()

    def test_combined_stop_and_bot_exit(self):
        self.native_fixture(combined=True)
        result = reconcile(self.client, self.report)
        self.assertEqual(result['status'], 'VERIFIED_FLAT')
        self.assertEqual(result['exit_source'], 'NATIVE_STOP_AND_BOT')
        self.assertEqual(len(result['orders']), 3)

    def test_native_short(self):
        self.native_fixture()
        self.report['plan']['direction'] = 'SHORT'
        self.orders[0]['side'] = 'sell'
        self.algo['side'] = self.child['side'] = 'buy'
        self.assertEqual(reconcile(self.client, self.report)['status'], 'VERIFIED_FLAT')

    def test_wrong_parent_or_child_link_is_rejected(self):
        for target, key, value in [('algo', 'algoClOrdId', 'other'), ('child', 'algoId', '999'), ('child', 'instId', 'ETH-USDT-SWAP')]:
            self.native_fixture()
            getattr(self, target)[key] = value
            with self.subTest(key=key), self.assertRaises(DemoError):
                reconcile(self.client, self.report)

    def test_duplicate_child_ids_rejected(self):
        self.native_fixture()
        self.algo['ordIdList'] = ['901', '901']
        with self.assertRaises(DemoError):
            reconcile(self.client, self.report)

    def test_untriggered_stop_is_not_exit_evidence(self):
        self.native_fixture()
        self.algo['state'] = 'live'
        with self.assertRaises(DemoError):
            reconcile(self.client, self.report)

    def test_partial_native_exit_does_not_settle(self):
        self.native_fixture()
        self.child['accFillSz'] = '.03'
        self.assertEqual(reconcile(self.client, self.report)['status'], 'NEEDS_REVIEW')

    def test_network_failure_does_not_fall_back(self):
        self.client.get.side_effect = [ [self.orders[0]], DemoError('Network failure') ]
        with self.assertRaises(DemoError):
            reconcile(self.client, self.report)
        self.assertEqual(self.client.get.call_count, 2)
