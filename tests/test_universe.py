import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from hedging_tools.universe import rank, select, crypto_swap
from hedging_tools.news import headline_matches
from hedging_tools.demo_execution import plan

NOW = datetime(2026, 10, 18, 13, tzinfo=timezone.utc)


def instrument(base):
    return {'instId': base+'-USDT-SWAP', 'instType': 'SWAP', 'ctType': 'linear', 'settleCcy': 'USDT',
            'ctValCcy': base, 'instCategory': '1', 'state': 'live', 'lever': '30', 'ctVal': '1',
            'ctMult': '1', 'lotSz': '.01', 'minSz': '.01', 'tickSz': '.01'}


def ticker(base, price=100, volume=200000):
    return {'instId': base+'-USDT-SWAP', 'ts': str(int(NOW.timestamp()*1000)),
            'last': str(price), 'bidPx': str(price), 'askPx': str(price*1.0005),
            'high24h': str(price*1.05), 'low24h': str(price*.95), 'volCcy24h': str(volume), 'vol24h': '999999999'}


class UniverseTests(unittest.TestCase):
    def test_cap_and_stable_tie_break(self):
        bases = ['C'+str(i).zfill(2) for i in range(30)]
        result = rank(list(map(instrument, bases)), list(map(ticker, reversed(bases))), NOW)
        self.assertEqual(len(result['pairs']), 20)
        self.assertEqual(result['pairs'], [x+'-USDT-SWAP' for x in bases[:20]])

    def test_volume_is_base_times_price_not_contract_count(self):
        result = rank([instrument('A'), instrument('B')], [ticker('A'), ticker('B', 1, 30000000)], NOW)
        self.assertEqual(result['pairs'][0], 'B-USDT-SWAP')
        self.assertEqual(result['selected'][0]['turnover_usdt_estimate'], '30000000')

    def test_non_crypto_inverse_and_inactive_excluded(self):
        items = [dict(instrument('A'), instCategory='2'), dict(instrument('B'), ctType='inverse'),
                 dict(instrument('C'), state='suspend'), instrument('D')]
        self.assertEqual(rank(items, list(map(ticker, 'ABCD')), NOW)['pairs'], ['D-USDT-SWAP'])
        self.assertFalse(crypto_swap(dict(instrument('D'), instCategory='')))

    def test_spread_liquidity_staleness_and_nan_rejected(self):
        quotes = [dict(ticker('A'), askPx='110'), ticker('B', 1, 100),
                  dict(ticker('C'), ts='0'), dict(ticker('D'), volCcy24h='NaN')]
        self.assertEqual(rank(list(map(instrument, 'ABCD')), quotes, NOW)['pairs'], [])

    def test_extreme_illiquid_move_cannot_enter_universe(self):
        quote = dict(ticker('A', 1, 1000), high24h='100000')
        self.assertEqual(rank([instrument('A')], [quote], NOW)['status'], 'EMPTY')

    def test_network_failure_never_returns_cached_or_fabricated_pairs(self):
        result = select(Mock(get=Mock(side_effect=OSError('offline'))))
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertEqual(result['pairs'], [])

    def test_dynamic_pair_can_use_existing_sizing_only_when_explicitly_allowed(self):
        metadata, quote = instrument('ADA'), ticker('ADA')
        signal = {'pair': metadata['instId'], 'action': 'LONG', 'stop': '99.9'}
        with self.assertRaises(ValueError):
            plan(metadata, quote, signal)
        result = plan(metadata, quote, signal, allowed_pairs=(metadata['instId'],))
        self.assertEqual(result['pair'], metadata['instId'])
        self.assertLessEqual(float(result['margin_upper_bound']), 5)
        self.assertLessEqual(float(result['planned_risk']), 1)

    def test_dynamic_news_does_not_match_ambiguous_common_words(self):
        self.assertFalse(headline_matches('ON-USDT-SWAP', 'Markets are on track'))
        self.assertFalse(headline_matches('ABC-USDT-SWAP', 'ABC announced earnings'))
        self.assertTrue(headline_matches('ABC-USDT-SWAP', '$ABC token update'))
        self.assertTrue(headline_matches('ABC-USDT-SWAP', 'ABC/USDT liquidity update'))
        self.assertTrue(headline_matches('ADA-USDT-SWAP', 'Cardano upgrade announced'))
