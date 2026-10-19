import tempfile
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, Mock
from hedging_tools.demo_agent import Budget, select, once, recover, NoTrade, cooldown_seconds
from hedging_tools.okx_demo import DemoError


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'budget.sqlite3'
        self.budget = Budget(self.path)
        self.now = datetime(2026, 10, 18, 2, tzinfo=timezone.utc)

    def test_budget_uses_fifty_not_exchange_balance(self):
        self.assertEqual(self.budget.reserve('a', 'journal', '290', self.now), '1')
        state = Budget(self.path).settle('289.95')
        self.assertEqual(state['equity'], '49.95')
        self.assertEqual(state['day_start_equity'], '50')

    def test_pending_survives_restart_and_blocks(self):
        self.budget.reserve('a', 'journal', '290', self.now)
        with self.assertRaises(ValueError):
            Budget(self.path).state(self.now + timedelta(days=1))
        with self.assertRaises(ValueError):
            Budget(self.path).reserve('b', 'journal', '290', self.now)

    def test_daily_loss_halt_and_next_day_baseline(self):
        self.budget.reserve('a', 'journal', '290', self.now)
        self.assertTrue(self.budget.settle('285')['halted'])
        with self.assertRaises(ValueError):
            self.budget.reserve('b', 'journal', '285', self.now)
        state = Budget(self.path).state(self.now + timedelta(days=1))
        self.assertFalse(state['halted'])
        self.assertEqual(state['day_start_equity'], '45')

    def test_remaining_daily_risk_is_capped(self):
        self.budget.reserve('a', 'journal', '290', self.now)
        self.budget.settle('285.3')
        self.assertEqual(self.budget.reserve('b', 'journal', '285.3', self.now), '0.3')

    def test_consumed_proposal_cannot_reopen(self):
        self.budget.reserve('a', 'journal', '290', self.now)
        self.budget.settle('290')
        with self.assertRaises(ValueError):
            Budget(self.path).reserve('a', 'journal', '290', self.now)

    def test_wait_does_not_construct_exchange_client(self):
        with patch('hedging_tools.demo_agent.ROOT', Path(self.tmp.name)), patch('hedging_tools.demo_agent.subprocess.run') as research, patch('hedging_tools.demo_agent.load_latest', return_value=('run', [])), patch('hedging_tools.demo_agent.OKXDemo') as client:
            research.return_value.returncode = 0
            result = once()
            self.assertEqual(result['status'], 'WAIT')
            self.assertEqual(result['orders_sent'], 0)
            client.assert_not_called()

    def test_unvalidated_proposal_is_skipped(self):
        self.assertIsNone(select([({}, {'status': 'ERROR'})], self.now))

    def test_stop_after_research_prevents_exchange_calls(self):
        with patch('hedging_tools.demo_agent.ROOT', Path(self.tmp.name)), patch('hedging_tools.demo_agent.subprocess.run') as research, patch('hedging_tools.demo_agent.load_latest', return_value=('run', [])), patch('hedging_tools.demo_agent.OKXDemo') as client:
            research.return_value.returncode = 0
            result = once(stop_requested=lambda: True)
            self.assertEqual(result['status'], 'STOPPED')
            self.assertEqual(result['orders_sent'], 0)
            client.assert_not_called()

    def test_plan_rejection_does_not_lock_budget(self):
        root = Path(self.tmp.name)
        client = Mock()
        client.get.return_value = []
        with patch('hedging_tools.demo_agent.ROOT', root), patch('hedging_tools.demo_agent.subprocess.run') as research, patch('hedging_tools.demo_agent.load_latest', return_value=('run', [])), patch('hedging_tools.demo_agent.select', return_value=('packet', {})), patch('hedging_tools.demo_agent.OKXDemo', return_value=client), patch('hedging_tools.demo_agent.equity', return_value=290), patch('hedging_tools.demo_agent.execute', side_effect=NoTrade('No risk-fitting size')):
            research.return_value.returncode = 0
            result = once()
        self.assertEqual(result['status'], 'WAIT')
        self.assertEqual(result['orders_sent'], 0)
        state = Budget(root / 'demo-strategy.sqlite3').snapshot()
        self.assertIsNone(state['pending'])
        self.assertEqual(state['equity'], '50')
        client.post.assert_not_called()

    def execute_mock_cycle(self, verification):
        client = Mock()
        client.get.return_value = []
        execution = {'status': 'COMPLETE', 'final_open_positions': 0,
                     'final_pending_orders': 0, 'final_own_stops': 0}
        with patch('hedging_tools.demo_agent.ROOT', Path(self.tmp.name)), patch('hedging_tools.demo_agent.subprocess.run') as research, patch('hedging_tools.demo_agent.load_latest', return_value=('run', [])), patch('hedging_tools.demo_agent.select', return_value=('packet', {})), patch('hedging_tools.demo_agent.OKXDemo', return_value=client), patch('hedging_tools.demo_agent.equity', return_value=290), patch('hedging_tools.demo_agent.execute', return_value=execution), patch('hedging_tools.demo_agent.reconcile', return_value=verification):
            research.return_value.returncode = 0
            return once()

    def test_normal_cycle_requires_verified_fills_and_pnl(self):
        verification = {'status': 'VERIFIED_FLAT', 'usdt_equity': '289.95',
                        'net_trade_pnl_excluding_funding_usdt': '-0.05'}
        result = self.execute_mock_cycle(verification)
        self.assertEqual(result['budget']['equity'], '49.95')
        database = Path(self.tmp.name) / 'demo-strategy.sqlite3'
        with closing(sqlite3.connect(database)) as db:
            rows = db.execute('SELECT net_change,evidence_json FROM verified_settlements').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], '-0.05')
        self.assertEqual(json.loads(rows[0][1])['reconciliation'], verification)
        self.assertIsNone(Budget(database).snapshot()['pending'])

    def test_flat_position_alone_cannot_release_budget(self):
        with self.assertRaises(DemoError):
            self.execute_mock_cycle({'status': 'NEEDS_REVIEW'})
        state = Budget(Path(self.tmp.name) / 'demo-strategy.sqlite3').snapshot()
        self.assertIsNotNone(state['pending'])
        self.assertEqual(state['equity'], '50')

    def test_unexplained_equity_change_blocks_normal_cycle(self):
        with self.assertRaises(DemoError):
            self.execute_mock_cycle({'status': 'VERIFIED_FLAT', 'usdt_equity': '300',
                                     'net_trade_pnl_excluding_funding_usdt': '-0.05'})
        self.assertIsNotNone(Budget(Path(self.tmp.name) / 'demo-strategy.sqlite3').snapshot()['pending'])

    def test_duplicate_verification_rolls_back_budget_update(self):
        self.budget.reserve('a', 'journal', '290', self.now)
        self.budget.settle('289.95', verification={'check': 'first'})
        self.budget.reserve('b', 'journal', '289.95', datetime.now(timezone.utc) + timedelta(minutes=16))
        with self.assertRaises(sqlite3.IntegrityError):
            self.budget.settle('289.90', verification={'check': 'duplicate'})
        state = self.budget.snapshot()
        self.assertEqual(state['equity'], '49.95')
        self.assertIsNotNone(state['pending'])

    def test_verified_trade_cooldown_survives_restart(self):
        self.budget.reserve('a', 'journal', '290', self.now)
        state = self.budget.settle('289.95', verification={'check': 'test'})
        deadline = datetime.fromisoformat(state['next_entry_after'])
        restarted = Budget(self.path)
        self.assertEqual(cooldown_seconds(restarted.snapshot(), deadline - timedelta(seconds=60)), 60)
        with self.assertRaises(ValueError):
            restarted.reserve('b', 'next', '289.95', deadline - timedelta(seconds=1))
        self.assertEqual(restarted.reserve('b', 'next', '289.95', deadline), '1')

    def test_cooldown_skips_research_and_exchange(self):
        root = Path(self.tmp.name)
        budget = Budget(root / 'demo-strategy.sqlite3')
        budget.reserve('a', 'journal', '290', datetime.now(timezone.utc))
        budget.settle('289.95', verification={'check': 'test'})
        with patch('hedging_tools.demo_agent.ROOT', root), patch('hedging_tools.demo_agent.subprocess.run') as research, patch('hedging_tools.demo_agent.OKXDemo') as client:
            result = once()
        self.assertEqual(result['status'], 'COOLDOWN')
        self.assertEqual(result['orders_sent'], 0)
        research.assert_not_called()
        client.assert_not_called()

    def prepare_recovery(self):
        journal = Path(self.tmp.name) / 'journal.json'
        journal.write_text(json.dumps({'purpose': 'AI_SIGNAL_EXECUTION_TEST'}))
        self.budget.reserve('a', journal, '290', self.now)
        return {'status': 'VERIFIED_FLAT', 'usdt_equity': '289.95',
                'net_trade_pnl_excluding_funding_usdt': '-0.05'}

    def test_recovery_commits_once_with_evidence(self):
        result = self.prepare_recovery()
        client = Mock()
        with patch('hedging_tools.demo_agent.reconcile', return_value=result):
            recovered = recover(self.budget, lambda: client, self.now)
        self.assertEqual(recovered['budget']['equity'], '49.95')
        self.assertEqual(recovered['budget']['last_recovery']['reconciliation'], result)
        factory = Mock()
        self.assertEqual(recover(Budget(self.path), factory, self.now)['status'], 'NO_PENDING_ATTEMPT')
        factory.assert_not_called()
        client.post.assert_not_called()

    def test_recovery_blocks_unexplained_account_delta(self):
        result = self.prepare_recovery()
        result['usdt_equity'] = '300'
        with patch('hedging_tools.demo_agent.reconcile', return_value=result), self.assertRaises(ValueError):
            recover(self.budget, Mock(), self.now)
        self.assertIsNotNone(self.budget.snapshot()['pending'])

    def test_recovery_blocks_remaining_exposure(self):
        result = self.prepare_recovery()
        result['status'] = 'NEEDS_REVIEW'
        with patch('hedging_tools.demo_agent.reconcile', return_value=result), self.assertRaises(ValueError):
            recover(self.budget, Mock(), self.now)
        self.assertIsNotNone(self.budget.snapshot()['pending'])

    def test_recovery_blocks_daily_boundary(self):
        self.prepare_recovery()
        factory = Mock()
        with self.assertRaises(ValueError):
            recover(self.budget, factory, self.now + timedelta(days=1))
        factory.assert_not_called()

    def test_settlement_cannot_clear_different_attempt(self):
        self.prepare_recovery()
        with self.assertRaises(ValueError):
            self.budget.settle('289.95', expected_journal='other.json')
        self.assertIsNotNone(self.budget.snapshot()['pending'])

    def test_recovery_api_failure_keeps_budget_locked(self):
        self.prepare_recovery()
        with patch('hedging_tools.demo_agent.reconcile', side_effect=ValueError('unavailable')), self.assertRaises(ValueError):
            recover(self.budget, Mock(), self.now)
        self.assertIsNotNone(self.budget.snapshot()['pending'])
