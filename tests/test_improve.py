import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from hedging_tools.improve import collect, build_report, evaluate_group, persist, load_rows, RULES, LOCKED
from hedging_tools.improve_evidence import snapshot


def evidence():
    return {'strategy_version': 'test-v1', 'captured_at': '2026-10-19T00:02:00+00:00',
            'market_observed_at': '2026-10-19T00:00:30+00:00',
            'metrics': {'15m': {'rvol': '1.1'}, 'spread_fraction': '.0008'},
            'ai_reason': 'Test evidence'}


def position():
    return {'id': 'p1', 'pair': 'BTC-USDT-SWAP', 'status': 'CLOSED', 'exit_kind': 'SL',
            'pnl': '-.82', 'risk_budget': '1', 'opened_at': 1000, 'closed_at': 2000,
            'entry_evidence': evidence(), 'execution_evidence': [
                {'role': 'ENTRY', 'state': 'filled', 'accFillSz': '1', 'feeCcy': 'USDT', 'fee': '-.01', 'pnl': '0'},
                {'role': 'SL', 'state': 'filled', 'accFillSz': '1', 'feeCcy': 'USDT', 'fee': '-.01', 'pnl': '-.8'}]}


def reference():
    return {'id': 's1', 'pair': 'BTC-USDT-SWAP', 'status': 'SL', 'quality': 'OK',
            'issued_ms': 1000, 'entry_evidence': evidence(), 'model': 'MARK_1M_REFERENCE_TOUCH_V1',
            'events': [{'event': 'SL', 'bar_end_ms': 2000}]}


def cohort(n=60, source='ACTUAL_DEMO'):
    return [{'id': str(i), 'source': source, 'version': 'v1', 'start': i*100, 'end': i*100+50,
             'outcome': 'SL' if i%5 == 0 else 'TP3', 'pnl': '-1' if i%5 == 0 else '1',
             'features': {'rvol': '1.1' if i%5 == 0 else '1.5', 'spread': '.0001', 'age_seconds': '10'}} for i in range(n)]


class ImproveTests(unittest.TestCase):
    def test_stop_diagnosis_does_not_claim_causality(self):
        report = build_report({'positions': [position()]}, [])
        review = report['reviews'][0]
        self.assertEqual(review['conclusion'], 'CAUSE_NOT_ESTABLISHED')
        self.assertEqual({f['kind'] for f in review['findings']}, {'LOW_RELATIVE_VOLUME', 'WIDE_ENTRY_SPREAD', 'OLDER_SNAPSHOT'})
        self.assertEqual(report['eligible_samples']['ACTUAL_DEMO'], 1)
        self.assertEqual(report['activation'], 'NOT_APPLIED')
        self.assertEqual(report['locked_risk'], LOCKED)

    def test_negative_pnl_without_stop_evidence_is_not_called_sl(self):
        p = position()
        p.pop('execution_evidence')
        report = build_report({'positions': [p]}, [])
        self.assertEqual(report['reviews'][0]['outcome'], 'UNKNOWN')
        self.assertEqual(report['eligible_samples']['ACTUAL_DEMO'], 0)

    def test_protected_stop_is_not_counted_as_initial_sl(self):
        p = position()
        p['exit_kind'] = 'PROTECTED_STOP'
        records, reviews, _ = collect({'positions': [p]}, [])
        self.assertEqual(reviews[0]['outcome'], 'PROTECTED_STOP')
        self.assertEqual(records[0]['outcome'], 'PROTECTED_STOP')

    def test_protection_failure_is_a_separate_execution_issue(self):
        p = position()
        p.update(exit_kind='PROTECTIVE_EXIT', protective_exit_reason='Missing native stop')
        report = build_report({'positions': [p]}, [])
        self.assertEqual(report['reviews'][0]['conclusion'], 'PROTECTION_ISSUE_RECORDED')
        self.assertEqual(report['eligible_samples']['ACTUAL_DEMO'], 0)

    def test_reference_and_actual_never_mix_pnl(self):
        report = build_report({'positions': [position()]}, [reference()], evaluate=True)
        self.assertEqual(len(report['evaluations']), 2)
        self.assertTrue(all(x['status'] == 'INSUFFICIENT_DATA' for x in report['evaluations']))
        self.assertIsNone(next(r for r in report['reviews'] if r['source'] == 'REFERENCE_SIGNAL')['pnl_excluding_funding'])

    def test_ambiguous_or_missing_reference_data_excluded(self):
        report = build_report({}, [dict(reference(), status='AMBIGUOUS'), dict(reference(), quality='DATA_GAP')], True)
        self.assertEqual(report['reviews'], [])
        self.assertEqual(report['evaluations'], [])

    def test_unmatched_cashflows_excluded_from_replay(self):
        p = position()
        p['pnl'] = '-10'
        report = build_report({'positions': [p]}, [], True)
        self.assertEqual(report['eligible_samples']['ACTUAL_DEMO'], 0)
        self.assertEqual(report['excluded']['ACTUAL_DEMO:invalid_evidence'], 1)

    def test_one_loss_cannot_trigger_strategy_change(self):
        report = build_report({'positions': [position()]}, [], True)
        self.assertEqual(report['evaluations'][0]['status'], 'INSUFFICIENT_DATA')
        self.assertNotIn('rule', report['evaluations'][0])

    def test_chronological_holdout_and_approval_required(self):
        result = evaluate_group(cohort())
        self.assertEqual(result['status'], 'NEEDS_USER_REVIEW')
        self.assertEqual(result['rule']['name'], 'MIN_RVOL_1_2')
        self.assertEqual(result['train_ids'], list(map(str, range(40))))
        self.assertEqual(result['holdout_ids'], list(map(str, range(40, 60))))
        self.assertEqual(result['activation'], 'NOT_APPLIED')
        self.assertEqual(result['holdout']['metric'], 'PNL_USDT_EXCLUDING_FUNDING')

    def test_failed_holdout_cannot_select_another_rule(self):
        rows = cohort()
        for r in rows[40:]:
            r['features']['rvol'] = '1.5' if r['outcome'] == 'SL' else '1.1'
        result = evaluate_group(rows)
        self.assertEqual(result['status'], 'HOLDOUT_FAILED')
        self.assertEqual(result['rule']['name'], 'MIN_RVOL_1_2')

    def test_overlapping_train_outcomes_purged_before_holdout(self):
        rows = cohort()
        rows[39]['end'] = rows[40]['start']+10
        result = evaluate_group(rows)
        self.assertEqual(result['purged_overlap'], 1)
        self.assertNotIn('39', result['train_ids'])

    def test_reference_evaluation_has_no_profit_metric(self):
        result = evaluate_group(cohort(source='REFERENCE_SIGNAL'))
        self.assertEqual(result['holdout']['metric'], 'INITIAL_SL_COUNT_NOT_PNL')

    def test_reject_everything_cannot_pass(self):
        rows = cohort()
        for r in rows:
            r['features']['rvol'] = '1.1'
        self.assertEqual(evaluate_group(rows)['status'], 'NO_SUPPORTED_CHANGE')

    def test_versions_are_not_pooled(self):
        a, b = position(), position()
        b['id'] = 'p2'
        b['entry_evidence']['strategy_version'] = 'test-v2'
        self.assertEqual(len(build_report({'positions': [a, b]}, [], True)['evaluations']), 2)

    def test_persistence_is_idempotent_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'improve.sqlite3'
            source = {'positions': [position()]}
            original = copy.deepcopy(source)
            report = build_report(source, [], True)
            persist(report, path)
            persist(report, path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM proposals').fetchone()[0], 1)
            self.assertEqual(source, original)

    def test_reader_does_not_create_missing_source_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'missing.sqlite3'
            self.assertEqual(load_rows(path, 'SELECT data FROM portfolio'), [])
            self.assertFalse(path.exists())

    def test_allowed_experiments_cannot_change_risk_or_exit_levels(self):
        self.assertEqual({r['feature'] for r in RULES}, {'rvol', 'spread', 'age_seconds'})
        self.assertTrue(all('stop' not in r and 'leverage' not in r for r in RULES))

    def test_snapshot_allowlists_input_and_does_not_copy_credentials(self):
        envelope = {'input': {'market': {'metrics': {}, 'candidate': {}}, 'secret_key': 'must-not-copy'}}
        proposal = {'packet_id': 'p', 'reason': 'r', 'invalidation': 'i', 'source_ids': []}
        result = snapshot(envelope, proposal, ticker={'bidPx': '1', 'askPx': '1.01', 'api_key': 'must-not-copy'})
        self.assertNotIn('must-not-copy', json.dumps(result))
        self.assertIn('strategy_version', result)
