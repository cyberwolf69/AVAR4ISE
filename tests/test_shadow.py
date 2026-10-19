from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from hedging_tools.shadow import Shadow
from hedging_tools.regime import describe
from hedging_tools import structure
from hedging_tools.scanner import screen
from tests.test_scanner import fixture, NOW, CONFIG
from tests.test_structure import rows


class RegimeTests(unittest.TestCase):
    def test_directional_and_choppy(self):
        r=describe(rows(range(100,170)))
        self.assertEqual(r['condition'],'DIRECTIONAL')
        self.assertEqual(r['net_direction'],'UP')
        self.assertEqual(describe(rows([100,101]*35))['condition'],'CHOPPY')
        self.assertEqual(r['role'],'CONTEXT_ONLY_NOT_ENTRY_GATE')

    def test_volatility_expansion_and_contraction(self):
        r=rows([100]*70)
        for i in range(56,70):
            t,h,l,c,v=r[i];r[i]=(t,h+10,l-10,c,v)
        self.assertEqual(describe(r)['volatility'],'EXPANDING')
        for i in range(28,56):
            t,h,l,c,v=r[i];r[i]=(t,h+100,l-90,c,v)
        self.assertEqual(describe(r)['volatility'],'CONTRACTING')

    def test_scanner_context_does_not_block_existing_candidate(self):
        result=screen(fixture(),CONFIG,NOW)
        self.assertEqual(result['status'],'WATCHLIST')
        self.assertIn('regime',result['metrics'])


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'scans.sqlite3'
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('CREATE TABLE scans (id INTEGER PRIMARY KEY,raw_json TEXT,result_json TEXT)')
        self.shadow=Shadow(self.root/'shadow',self.source,'test')

    def tearDown(self):
        self.temp.cleanup()

    def insert(self,when=NOW):
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('INSERT INTO scans(raw_json,result_json) VALUES (?,?)',
                       (json.dumps(fixture()),json.dumps({'observed_at':when.isoformat()})))

    def candidate(self):
        return {'pair':'BTC-USDT-SWAP','observed_at':NOW.isoformat(),'status':'WATCHLIST',
                'metrics':{'structure':{'bos_time':123}},
                'candidate':{'action':'LONG','entry':'100','stop_reference':'99'},'reasons':['Passed']}

    def test_forward_only_restart_and_source_readonly(self):
        self.insert()
        self.assertEqual(self.shadow.collect(NOW)['after_scan_id'],1)
        self.assertEqual(self.shadow.report()['models']['baseline']['observations'],0)
        self.insert();before=self.source.read_bytes()
        original=structure.trend
        with patch('hedging_tools.shadow.screen',side_effect=lambda *a,**k:self.candidate()):
            self.shadow.collect(NOW)
            self.insert()
            self.shadow.collect(NOW)
        self.assertIs(structure.trend,original)
        for history in self.shadow.histories.values():
            self.assertEqual(len(history.records()),1)
            self.assertGreaterEqual(history.records()[0]['observation_start_ms'],int(NOW.timestamp()*1000))
            self.assertEqual(history.records()[0]['actual_trade_pnl'],'NOT_COMPUTED')
        before=self.source.read_bytes()
        other=Shadow(self.root/'shadow',self.source,'test')
        self.assertEqual(other.collect(NOW)['scans_consumed'],0)
        self.assertEqual(before,self.source.read_bytes())

    def test_stale_new_rows_not_backfilled(self):
        self.shadow.collect(NOW)
        self.insert(NOW-timedelta(minutes=4))
        with patch('hedging_tools.shadow.screen') as scan:
            self.shadow.collect(NOW)
            scan.assert_not_called()
        self.assertEqual(self.shadow.report()['models']['baseline']['total'],0)

    def test_separate_model_reference_ledgers(self):
        self.shadow.collect(NOW);self.insert()
        blocked={'status':'WAIT','reasons':['No structure']}
        with patch('hedging_tools.shadow.screen',side_effect=[blocked,self.candidate()]):
            self.shadow.collect(NOW)
        report=self.shadow.report()
        self.assertEqual(report['models']['baseline']['total'],0)
        self.assertEqual(report['models']['break_bias']['total'],1)
        self.assertEqual(report['activation'],'NOT_APPLIED')

    def test_new_experiment_skips_existing_history(self):
        self.insert()
        newer=Shadow(self.root/'shadow',self.source,'new-version')
        self.assertEqual(newer.collect(NOW)['after_scan_id'],1)
        self.assertEqual(newer.report()['models']['break_bias']['total'],0)

    def test_retry_after_ledger_cursor_loss_deduplicates_bos(self):
        self.shadow.collect(NOW);self.insert()
        with patch('hedging_tools.shadow.screen',side_effect=lambda *a,**k:self.candidate()):
            self.shadow.collect(NOW)
            with closing(sqlite3.connect(self.shadow.path)) as db,db:
                db.execute('DELETE FROM events')
                db.execute("UPDATE meta SET value='0' WHERE key='cursor'")
            self.shadow.collect(NOW+timedelta(seconds=30))
        self.assertEqual(len(self.shadow.histories['break_bias'].records()),1)

    def test_outcomes_poll_is_throttled_across_restart(self):
        self.assertTrue(self.shadow.outcomes_due(NOW))
        other=Shadow(self.root/'shadow',self.source,'test')
        self.assertFalse(other.outcomes_due(NOW+timedelta(seconds=10)))
        self.assertTrue(other.outcomes_due(NOW+timedelta(seconds=60)))
