import math
import random
import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from hedging_tools.mean_reversion import analyze, forward_diagnostics
from hedging_tools.reversion_research import run
from tests.test_scanner import fixture, NOW


def synthetic(phi=.8, count=601):
    rng, value = random.Random(42), math.log(100)
    prices = []
    for _ in range(count):
        value = math.log(100) * (1 - phi) + phi * value + rng.gauss(0, .002)
        prices.append(math.exp(value))
    return prices


class ReversionTests(unittest.TestCase):
    def test_known_ou_fit_and_correct_stationary_scale(self):
        result = analyze(synthetic(), 500)
        self.assertNotEqual(result['status'], 'REJECTED')
        self.assertAlmostEqual(result['phi'], .8, delta=.1)
        expected = result['innovation_sd'] / math.sqrt(1 - result['phi'] ** 2)
        self.assertAlmostEqual(result['stationary_sd'], expected)
        self.assertAlmostEqual(result['half_life_bars'], math.log(2) / -math.log(result['phi']))
        self.assertEqual(result['execution'], 'DISABLED')

    def test_scoring_candle_not_used_to_fit(self):
        prices = synthetic()
        first = analyze(prices)
        prices[-1] *= 1.1
        changed = analyze(prices)
        for key in ('phi', 'mu_log', 'stationary_sd', 'hurst'):
            self.assertEqual(first[key], changed[key])
        self.assertNotEqual(first['zeta'], changed['zeta'])

    def test_price_scale_invariance(self):
        prices = synthetic()
        a, b = analyze(prices), analyze([x * 100 for x in prices])
        self.assertAlmostEqual(a['zeta'], b['zeta'], places=8)

    def test_negative_phi_rejected_without_mean(self):
        result = analyze(synthetic(phi=-.8))
        self.assertEqual(result['status'], 'REJECTED')
        self.assertNotIn('mu_log', result)

    def test_degenerate_and_invalid_inputs_rejected(self):
        for values in ([100] * 121, [100] * 120, synthetic()[:-1] + [float('nan')], synthetic()[:-1] + [0]):
            self.assertEqual(analyze(values)['status'], 'REJECTED')

    def test_candidate_direction_and_hurst_gate(self):
        prices = synthetic()
        for factor, expected in ((2, 'SHORT'), (.5, 'LONG')):
            with patch('hedging_tools.mean_reversion.hurst_rs', return_value=.3):
                result = analyze(prices[:-1] + [prices[-1] * factor])
                self.assertEqual(result['candidate'], expected)
            with patch('hedging_tools.mean_reversion.hurst_rs', return_value=.6):
                self.assertEqual(analyze(prices[:-1] + [prices[-1] * factor])['candidate'], 'NONE')

    def test_walk_forward_has_no_future_training_and_nonoverlap(self):
        prices = list(range(100, 240))
        with patch('hedging_tools.mean_reversion.analyze', return_value={'status': 'CANDIDATE', 'candidate': 'LONG'}) as model:
            result = forward_diagnostics(prices, horizon=4)
        self.assertEqual(model.call_args_list[0].args[0], prices[:121])
        indices = [o['signal_index'] for o in result['outcomes']]
        self.assertTrue(all(b - a >= 4 for a, b in zip(indices, indices[1:])))
        self.assertAlmostEqual(result['outcomes'][0]['return_after_assumed_cost'], prices[124] / prices[120] - 1 - .002)
        self.assertFalse(result['funding_included'])
        self.assertFalse(result['execution_simulated'])

    def test_no_candidates_is_not_zero_profit(self):
        result = forward_diagnostics([100] * 150)
        self.assertEqual(result['candidate_count'], 0)
        self.assertIsNone(result['mean_return_after_assumed_cost'])

    def test_public_only_research_persists_rejections(self):
        client = Mock()
        client.get.return_value = fixture()['candles_15m']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research.sqlite3'
            report = run(client, path, NOW)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM reversion_runs').fetchone()[0], 2)
        self.assertEqual(report['execution'], 'DISABLED')
        self.assertTrue(all(r['analysis']['status'] == 'REJECTED' for r in report['results']))
        client.post.assert_not_called()
        self.assertTrue(all(c.args[0] == 'candles' for c in client.get.call_args_list))
