import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from hedging_tools.portfolio import Store, Portfolio, active, entry_allowance, refresh_budget, order_pnl
from hedging_tools.position_policy import split_size, targets, protective_stop
from hedging_tools.risk_engine import number as D
from hedging_tools.okx_demo import DemoError
from hedging_tools.analyst import make_packet
from hedging_tools.portfolio_runner import loop


class Exchange:
    def __init__(self, stage=0):
        self.stage = stage
        self.stop = '99'
        self.mutations = []
        self.canceled = False
        self.mismatch = False
        self.confirm_amend = True
        self.now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def order(self, oid, size, pnl, side):
        return {'ordId': oid, 'instId': 'BTC-USDT-SWAP', 'clOrdId': 'hp1' if oid == '1' else '',
                'state': 'filled', 'accFillSz': str(size), 'avgPx': '100', 'fee': '-0.01', 'feeCcy': 'USDT',
                'pnl': str(pnl), 'side': side, 'tdMode': 'isolated', 'cTime': str(self.now_ms-10000), 'uTime': str(self.now_ms)}

    def get(self, endpoint, **params):
        if endpoint == 'order':
            if params.get('clOrdId') == 'hp1':
                return [self.order('1', 1, 0, 'buy')]
            oid = params['ordId']
            i = int(oid) - 10
            row = self.order(oid, ['.4', '.35', '.25'][i-1], ['.4', '.7', '.75'][i-1], 'sell')
            row['algoId'] = str(100+i)
            return [row]
        if endpoint == 'positions':
            if self.stage == 3:
                return []
            q = ['1', '.6', '.25'][self.stage]
            mark = ['100', '101.2', '102.2'][self.stage]
            return [{'instId': 'BTC-USDT-SWAP', 'pos': q if not self.mismatch else '2', 'mgnMode': 'isolated',
                     'avgPx': '100', 'posId': 'owned1', 'margin': '3', 'lever': '30', 'liqPx': '90', 'markPx': mark,
                     'upl': str((D(mark)-100)*D(q))}]
        if endpoint == 'algo':
            suffix = params['algoClOrdId'][3:]
            base = {'instId': 'BTC-USDT-SWAP', 'algoClOrdId': params['algoClOrdId'], 'state': 'live', 'ordIdList': []}
            if suffix == 's':
                return [dict(base, algoId='100', slTriggerPx=self.stop, slOrdPx='-1', slTriggerPxType='mark', sz='1')]
            i = int(suffix[1:])
            return [dict(base, algoId=str(100+i), state='effective' if self.stage >= i else 'live',
                         ordIdList=[str(10+i)] if self.stage >= i else [],
                         tpTriggerPx=str(100+i), tpOrdPx='-1', tpTriggerPxType='mark')]
        if endpoint == 'algos':
            return [] if self.canceled else [{'algoId': '100', 'algoClOrdId': 'hp1s'}]
        if endpoint == 'bills':
            return [{'billId': '42', 'instId': 'BTC-USDT-SWAP', 'ccy': 'USDT', 'type': '8',
                     'ts': str(self.now_ms-1000), 'balChg': '-0.05'}]
        raise AssertionError(endpoint)

    def post(self, endpoint, body):
        self.mutations.append((endpoint, body))
        if endpoint == 'amend_algo' and self.confirm_amend:
            self.stop = body['newSlTriggerPx']
        if endpoint == 'cancel_algos':
            self.canceled = True
        return [{'sCode': '0'}]


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'p.sqlite3')
        state = self.store.read()
        state['positions'] = [{'id': 'hp1', 'pair': 'BTC-USDT-SWAP', 'direction': 'LONG', 'size': '1',
                              'status': 'ENTRY_PENDING', 'risk_budget': '1.2', 'stop': '99', 'leverage': '30', 'tick': '.01', 'unit': '1',
                              'legs': [{'size': s, 'price': str(101+i)} for i, s in enumerate(('.4', '.35', '.25'))]}]
        self.store.save(state)
        self.client = Exchange()
        self.manager = Portfolio(self.store, self.client, Mock())

    def test_three_stages_keep_slot_until_all_closed_and_survive_restart(self):
        self.assertEqual(self.manager.manage()['active_positions'], 1)
        self.client.stage = 1
        self.assertEqual(self.manager.manage()['active_positions'], 1)
        self.assertEqual(D(self.client.stop), D('100.20'))
        self.manager = Portfolio(Store(self.store.path), self.client, Mock())
        self.client.stage = 2
        self.assertEqual(self.manager.manage()['active_positions'], 1)
        self.assertEqual(D(self.client.stop), D('101'))
        self.client.stage = 3
        result = self.manager.manage()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['active_positions'], 0)
        closed = self.store.read()['positions'][0]
        self.assertEqual(closed['exit_kind'], 'TP3')
        self.assertEqual([o['role'] for o in closed['execution_evidence']], ['ENTRY', 'TP1', 'TP2', 'TP3'])
        self.assertEqual(len(closed['stop_history']), 2)
        self.assertEqual(closed['funding_bills'], {'42': '-0.05'})
        self.assertEqual(D(result['equity']), D('51.76'))  # 1.85 gross - .04 fee - .05 funding
        self.assertEqual(self.manager.manage()['equity'], result['equity'])

    def test_daily_halt_keeps_managing_tp_and_is_latched(self):
        s = self.store.read()
        s.update(day=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Jakarta')).date().isoformat(), halted=True)
        self.store.save(s)
        self.client.stage = 1
        result = self.manager.manage()
        self.assertTrue(result['daily_halted'])
        self.assertEqual(D(self.client.stop), D('100.2'))
        self.assertEqual(entry_allowance(self.store.read(), 'ETH-USDT-SWAP', [], [])[1], 'DAILY_LOSS_LIMIT')

    def test_unconfirmed_amend_blocks_entry_and_retries_only_idempotent_amend(self):
        self.client.stage, self.client.confirm_amend = 1, False
        result = self.manager.manage()
        self.assertEqual(result['status'], 'ENTRY_BLOCKED')
        self.assertIn('not confirmed', result['errors'][0]['reason'])
        self.assertTrue(all(op == 'amend_algo' for op, body in self.client.mutations))

    def test_unowned_size_never_auto_closes(self):
        self.client.mismatch = True
        self.assertEqual(self.manager.manage()['status'], 'ENTRY_BLOCKED')
        self.assertEqual(self.client.mutations, [])

    def test_missing_funding_gap_blocks_entries_but_manages_stops(self):
        state = self.store.read()
        state['funding_checked'] -= 7*86400000
        self.store.save(state)
        self.client.stage = 1
        self.assertEqual(self.manager.manage()['status'], 'ENTRY_BLOCKED')
        self.assertEqual(D(self.client.stop), D('100.2'))

    def test_funding_is_idempotent_and_unrelated_bills_ignored(self):
        self.manager.manage()
        first = self.store.read()['equity']
        self.manager.manage()
        self.assertEqual(self.store.read()['equity'], first)
        self.assertEqual(self.store.read()['funding'], {'42': '-0.05'})

    def test_error_on_one_position_does_not_skip_other_positions(self):
        state = self.store.read()
        state['positions'].append(dict(state['positions'][0], id='hp2', pair='ETH-USDT-SWAP'))
        self.store.save(state)
        with patch.object(self.manager, 'reconcile_one', side_effect=[DemoError('missing'), None]) as fn:
            self.manager.manage()
            self.assertEqual(fn.call_count, 2)

    def test_emergency_order_is_never_blindly_repeated(self):
        state = self.store.read()
        p = state['positions'][0]
        self.client.post = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            self.manager.emergency_exit(state, p, D(1), 'stop missing')
        state = self.store.read()
        with self.assertRaises(DemoError):
            self.manager.emergency_exit(state, state['positions'][0], D(1), 'stop missing')
        self.client.post.assert_called_once()
        self.assertTrue(self.client.post.call_args.args[1]['reduceOnly'])

    def test_native_stop_after_tp1_frees_slot_only_with_matching_exit_fills(self):
        self.client.stage = 1
        self.manager.manage()
        original_get = self.client.get
        def get(endpoint, **params):
            if endpoint == 'positions':
                return []
            if endpoint == 'algo' and params.get('algoClOrdId') == 'hp1s':
                return [{'instId': 'BTC-USDT-SWAP', 'algoClOrdId': 'hp1s', 'algoId': '100',
                         'state': 'effective', 'ordIdList': ['20']}]
            if endpoint == 'order' and params.get('ordId') == '20':
                return [dict(self.client.order('20', '.6', '.12', 'sell'), algoId='100')]
            return original_get(endpoint, **params)
        self.client.get = get
        result = self.manager.manage()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['active_positions'], 0)
        self.assertEqual(D(result['equity']), D('50.44'))
        self.assertEqual(self.store.read()['positions'][0]['exit_kind'], 'PROTECTED_STOP')

    def test_missing_stop_closes_once_then_reconciles_after_restart(self):
        original_get = self.client.get
        closed = False
        def get(endpoint, **params):
            if endpoint == 'positions' and closed:
                return []
            if endpoint == 'algo' and params.get('algoClOrdId') == 'hp1s':
                return []
            if endpoint == 'order' and params.get('clOrdId') == 'hp1x':
                return [dict(self.client.order('20', '1', '-.1', 'sell'), clOrdId='hp1x')]
            return original_get(endpoint, **params)
        self.client.get = get
        self.assertEqual(self.manager.manage()['status'], 'ENTRY_BLOCKED')
        self.assertEqual(self.client.mutations[0][0], 'order')
        closed = True
        restarted = Portfolio(Store(self.store.path), self.client, Mock())
        result = restarted.manage()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['active_positions'], 0)
        self.assertEqual(sum(op == 'order' for op, _ in self.client.mutations), 1)

    def test_cancelled_unfilled_fok_releases_slot_without_exit_order(self):
        original_get = self.client.get
        def get(endpoint, **params):
            if endpoint == 'order':
                return [dict(self.client.order('1', 0, 0, 'buy'), state='canceled')]
            return original_get(endpoint, **params)
        self.client.get = get
        self.assertEqual(self.manager.manage()['active_positions'], 0)
        self.assertEqual(self.client.mutations, [])

    def test_partial_fok_or_unmatched_close_never_frees_slot(self):
        original_get = self.client.get
        def get(endpoint, **params):
            if endpoint == 'order' and params.get('clOrdId') == 'hp1':
                return [self.client.order('1', '.5', 0, 'buy')]
            return original_get(endpoint, **params)
        self.client.get = get
        result = self.manager.manage()
        self.assertEqual(result['status'], 'ENTRY_BLOCKED')
        self.assertEqual(result['active_positions'], 1)
        self.assertEqual(self.client.mutations, [])

    def test_changed_position_identity_never_touched(self):
        self.manager.manage()
        original_get = self.client.get
        def get(endpoint, **params):
            rows = original_get(endpoint, **params)
            if endpoint == 'positions':
                rows[0]['posId'] = 'manual-replacement'
            return rows
        self.client.get = get
        self.client.stage = 1
        self.assertEqual(self.manager.manage()['status'], 'ENTRY_BLOCKED')
        self.assertEqual(self.client.mutations, [])

    def test_stop_crossed_while_exchange_still_reports_live_uses_reduce_only(self):
        original_get = self.client.get
        def get(endpoint, **params):
            rows = original_get(endpoint, **params)
            if endpoint == 'positions':
                rows[0]['markPx'] = '98.5'
            return rows
        self.client.get = get
        result = self.manager.manage()
        self.assertEqual(result['status'], 'ENTRY_BLOCKED')
        self.assertTrue(self.client.mutations[-1][1]['reduceOnly'])

    def test_pending_legacy_budget_not_reset(self):
        with self.assertRaises(ValueError):
            Store(self.root / 'blocked.sqlite3', {'pending': {'journal': 'old'}})

    def test_legacy_equity_and_halt_are_preserved(self):
        s = Store(self.root / 'legacy.sqlite3', {'equity': '45', 'day_start_equity': '50', 'halted': True, 'day': '2026-10-18'}).read()
        self.assertEqual(s['initial'], '45')
        self.assertTrue(s['halted'])


class PolicyTests(unittest.TestCase):
    def state(self):
        return {'initial': '50', 'equity': '50', 'day_start': '50', 'day': '2026-10-18', 'halted': False, 'positions': [], 'funding': {}}

    def test_split_uses_lots_and_total_is_exact(self):
        self.assertEqual(split_size('0.06', '.01', '.01'), [D('.02'), D('.02'), D('.02')])
        with self.assertRaises(ValueError):
            split_size('.02', '.01', '.01')

    def test_short_targets_and_stop_direction(self):
        self.assertEqual(targets(100, 101, 'SHORT'), [D(99), D(98), D(97)])
        self.assertEqual(protective_stop(100, 101, 'SHORT', 1, '.1'), D('99.8'))
        self.assertEqual(protective_stop(100, 101, 'SHORT', 2, '.1'), D('99'))

    def test_five_slots_and_freed_slot(self):
        s = self.state()
        s['positions'] = [{'pair': str(i), 'status': 'OPEN', 'remaining_risk': '.2', 'margin': '5'} for i in range(5)]
        self.assertEqual(entry_allowance(s, 'new', [], [])[1], 'POSITION_SLOTS_FULL')
        s['positions'][0]['status'] = 'CLOSED'
        self.assertEqual(entry_allowance(s, 'new', [], [])[0], D(1))
        s['halted'] = True
        self.assertEqual(entry_allowance(s, 'new', [], [])[0], D(0))

    def test_aggregate_loss_budget_includes_remaining_risk(self):
        s = self.state()
        s['equity'] = '46.4'
        s['positions'] = [{'pair': 'BTC', 'status': 'OPEN', 'remaining_risk': '1', 'margin': '5'}]
        self.assertEqual(entry_allowance(s, 'ETH', [], [])[0], D('.4'))
        s['positions'][0]['remaining_risk'] = '1.5'
        self.assertEqual(entry_allowance(s, 'ETH', [], [])[0], D(0))

    def test_duplicate_pair_and_manual_positions_block_entry(self):
        s = self.state()
        s['positions'] = [{'pair': 'BTC', 'status': 'OPEN', 'remaining_risk': '1', 'margin': '5'}]
        self.assertEqual(entry_allowance(s, 'BTC', [], [])[1], 'PAIR_ALREADY_OPEN')
        self.assertEqual(entry_allowance(s, 'ETH', [{'instId': 'SOL', 'pos': '1'}], [])[1], 'UNMANAGED_EXPOSURE_OR_PENDING_ORDER')

    def test_unrealized_losses_latch_day_and_recovery_cannot_unhalt(self):
        s = self.state()
        s['positions'] = [{'status': 'OPEN', 'pnl': '-1', 'upl': '-4'}]
        now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        refresh_budget(s, now)
        self.assertTrue(s['halted'])
        s['positions'][0]['upl'] = '0'
        refresh_budget(s, now)
        self.assertTrue(s['halted'])
        refresh_budget(s, now + timedelta(days=1))
        self.assertFalse(s['halted'])

    def test_rollover_does_not_erase_unobserved_overnight_loss(self):
        s = self.state()
        s['positions'] = [{'status': 'OPEN', 'pnl': '0', 'upl': '-6'}]
        refresh_budget(s, datetime(2026, 10, 19, 1, tzinfo=timezone.utc))
        self.assertEqual(s['day_start'], '50')
        self.assertTrue(s['halted'])

    def test_unknown_fee_cannot_create_available_budget(self):
        with self.assertRaises(DemoError):
            order_pnl({'accFillSz': '1', 'pnl': '5', 'feeCcy': 'BTC', 'fee': '0'})


class EntryTests(unittest.TestCase):
    def test_ambiguous_entry_intent_survives_restart_and_never_resubmits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'p.sqlite3')
            client, public = Mock(), Mock()
            now = datetime.now(timezone.utc)
            instrument = {'instId': 'BTC-USDT-SWAP', 'ctType': 'linear', 'settleCcy': 'USDT', 'ctValCcy': 'BTC',
                          'state': 'live', 'ctVal': '1', 'ctMult': '1', 'tickSz': '.01', 'lotSz': '.01', 'minSz': '.01', 'lever': '30'}
            def get(name, **kw):
                return {'config': [{'posMode': 'net_mode'}], 'positions': [], 'orders': [], 'algos': [],
                        'balance': [{'details': [{'ccy': 'USDT', 'availBal': '1000'}]}], 'instruments': [instrument]}[name]
            client.get.side_effect = get
            public.get.return_value = [{'instId': 'BTC-USDT-SWAP', 'bidPx': '100', 'askPx': '100.01', 'ts': str(int(now.timestamp()*1000))}]
            client.post.side_effect = [[{'sCode': '0'}], TimeoutError()]
            manager = Portfolio(store, client, public, clock=lambda: now)
            envelope = make_packet({'pair': 'BTC-USDT-SWAP', 'status': 'WATCHLIST', 'candidate': {'action': 'LONG', 'stop_reference': '99.9'}},
                                   [{'id': 'n'}], [{'status': 'OK'}], now)
            proposal = {'packet_id': envelope['input']['packet_id'], 'pair': 'BTC-USDT-SWAP', 'action': 'LONG',
                        'reason': 'test', 'invalidation': 'test', 'source_ids': ['n'], 'expires_at': envelope['input']['expires_at']}
            with patch.object(manager, 'manage'):
                with self.assertRaises(TimeoutError):
                    manager.enter(envelope, {'status': 'VALIDATED', 'proposal': proposal})
                self.assertEqual(len(active(Store(store.path).read())), 1)
                self.assertEqual(manager.enter(envelope, {'status': 'VALIDATED', 'proposal': proposal})['reason'], 'PROPOSAL_ALREADY_CONSUMED')
            self.assertEqual(client.post.call_count, 2)
            body = client.post.call_args.args[1]
            self.assertEqual(body['ordType'], 'fok')
            self.assertEqual(len(body['attachAlgoOrds']), 4)
            self.assertEqual(sum(D(a['sz']) for a in body['attachAlgoOrds'][:3]), D(body['sz']))


class LoopTests(unittest.TestCase):
    def test_halt_or_full_slots_never_prevents_research_and_position_management(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, stop = Mock(), Mock()
            stop.is_set.return_value = False
            manager.manage.return_value = {'status': 'MANAGING', 'daily_halted': True, 'active_positions': 5}
            def launch(output):
                output.write_text(json.dumps({'packets': [{'input': {'pair': 'BTC'}}], 'analyses': [{}]}))
                child = Mock()
                child.poll.return_value = 0
                return child
            manager.enter.return_value = {'status': 'WAIT', 'reason': 'DAILY_LOSS_LIMIT'}
            with patch('hedging_tools.portfolio_runner.RUNTIME_DIR', Path(tmp)):
                report = loop(manager, stop, cycles=1, launch=launch)
            self.assertEqual(report['status'], 'CYCLE_LIMIT_REACHED')
            self.assertGreaterEqual(manager.manage.call_count, 2)
            manager.enter.assert_called_once()

    def test_corrupt_research_output_never_trades_and_supervision_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, stop = Mock(), Mock()
            stop.is_set.return_value = False
            manager.manage.return_value = {'status': 'MANAGING'}
            def launch(output):
                output.write_text('{broken')
                child = Mock()
                child.poll.return_value = 0
                return child
            with patch('hedging_tools.portfolio_runner.RUNTIME_DIR', Path(tmp)):
                report = loop(manager, stop, cycles=2, launch=launch, monotonic=Mock(side_effect=range(0, 10000, 100)))
            self.assertEqual(report['completed_cycles'], 2)
            manager.enter.assert_not_called()
            self.assertGreaterEqual(manager.manage.call_count, 3)

    def test_stop_does_not_liquidate_positions(self):
        manager, stop = Mock(), Mock()
        stop.is_set.return_value = True
        self.assertEqual(loop(manager, stop)['status'], 'STOPPED')
        manager.enter.assert_not_called()
        manager.manage.assert_not_called()
