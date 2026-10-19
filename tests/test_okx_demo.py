import base64
import hashlib
import hmac
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from hedging_tools.okx_demo import OKXDemo, KEYS, DemoError


class DemoTests(unittest.TestCase):
    def test_demo_header_and_query_signature(self):
        opener = Mock(return_value=io.BytesIO(b'{"code":"0","data":[]}'))
        keys = dict(zip(KEYS, ("test-key", "test-secret", "test-pass")))
        client = OKXDemo(keys, opener, lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
        self.assertEqual(client.get("balance", ccy="USDT"), [])
        request = opener.call_args.args[0]
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["x-simulated-trading"], "1")
        self.assertEqual(request.get_method(), "GET")
        expected = base64.b64encode(hmac.new(b"test-secret", b"2026-10-18T00:00:00.000ZGET/api/v5/account/balance?ccy=USDT", hashlib.sha256).digest()).decode()
        self.assertEqual(headers["ok-access-sign"], expected)

    def test_order_route_not_exposed(self):
        opener = Mock()
        with self.assertRaises(KeyError):
            OKXDemo({}, opener).get("place_order")
        opener.assert_not_called()

    def test_provider_message_not_exposed(self):
        opener = Mock(return_value=io.BytesIO(b'{"code":"50101","msg":"secret"}'))
        with self.assertRaises(DemoError) as caught:
            OKXDemo(dict(zip(KEYS, ("a", "b", "c"))), opener).get("balance")
        self.assertIn("50101", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(caught.exception.api_code, '50101')

    def test_algo_details_read_only_demo_route(self):
        opener = Mock(return_value=io.BytesIO(b'{"code":"0","data":[]}'))
        client = OKXDemo(dict(zip(KEYS, ('a', 'b', 'c'))), opener)
        client.get('algo', algoClOrdId='tests')
        request = opener.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/api/v5/trade/order-algo?algoClOrdId=tests'))
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual({k.lower(): v for k, v in request.header_items()}['x-simulated-trading'], '1')
