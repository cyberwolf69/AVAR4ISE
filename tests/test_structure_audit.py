import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from hedging_tools.structure_audit import audit, break_bias
from hedging_tools import structure
from tests.test_structure import rows
from tests.test_scanner import fixture, NOW


class AuditTests(unittest.TestCase):
    def test_break_requires_previously_confirmed_pivot(self):
        r=rows([100,101,104,102,101,102,103,105])
        self.assertEqual(break_bias(r[:-1]),0)
        self.assertEqual(break_bias(r),1)
        r+=rows([100])
        self.assertEqual(break_bias(r),-1)

    def test_long_short_symmetry(self):
        r=rows([100,101,104,102,101,102,103,105])
        mirror=[(t,220-lo,220-hi,220-c,v) for t,hi,lo,c,v in r]
        self.assertEqual(break_bias(mirror),-1)

    def test_replay_is_readonly_and_restores_detector(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'scans.sqlite3'
            result={'observed_at':NOW.isoformat(),'metrics':{'structure':{'model':'structure_bos_retest_v1'}}}
            with closing(sqlite3.connect(db)) as c,c:
                c.execute('CREATE TABLE scans (id INTEGER, recorded_at TEXT,raw_json TEXT,result_json TEXT)')
                for i in range(2):
                    c.execute('INSERT INTO scans VALUES (?,?,?,?)',(i,NOW.isoformat(),json.dumps(fixture()),json.dumps(result)))
            before=db.read_bytes();detector=structure.trend
            report=audit(db)
            self.assertEqual(before,db.read_bytes())
            self.assertIs(structure.trend,detector)
            self.assertEqual(report['evaluated_observations'],1)
            self.assertEqual(report['identical_snapshots_skipped'],1)
            self.assertEqual(report['activation'],'NOT_APPLIED')
            self.assertEqual(set(report['models']),{'current_hh_hl','research_confirmed_break_bias'})

    def test_missing_database_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'absent.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                audit(db)
            self.assertFalse(db.exists())
