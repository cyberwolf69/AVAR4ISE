import copy
from contextlib import closing
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from hedging_tools.ai_client import AIClient, AIBackoff, AIError
from hedging_tools.analyst import make_packet, validate_proposal
from hedging_tools.provider_health import ProviderHealth
from hedging_tools.setup_memory import SetupMemory


class AgentReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'scans.sqlite3'
        self.now=datetime(2026,10,21,0,0,tzinfo=timezone.utc)
        self.market={'pair':'BTC-USDT-SWAP','status':'WATCHLIST',
            'metrics':{'structure':{'model':'structure_bos_retest_v1','bos_time':123,'htf_direction':1}},
            'candidate':{'action':'LONG','entry':'100','stop_reference':'99'}}
        self.memory=SetupMemory(self.path,'v1')
        self.envelope=make_packet(self.market,[],[],self.now,memory=self.memory.context(self.market,self.now))
        data=self.envelope['input']
        self.proposal={'packet_id':data['packet_id'],'pair':data['pair'],'action':'LONG',
            'reason':'Confirmed setup. News unavailable.','invalidation':'Support breaks.',
            'source_ids':[],'expires_at':data['expires_at'],
            'supporting_evidence':[{'observation':'Screened long','ref':'/market/candidate/action'}],
            'opposing_evidence':[],'wait_for':''}

    def test_structured_decision_and_legacy_compatibility(self):
        self.assertEqual(validate_proposal(self.proposal,self.envelope,self.now)['signal']['action'],'LONG')
        legacy=make_packet(self.market,[],[],self.now)
        p={k:v for k,v in self.proposal.items() if k not in ('supporting_evidence','opposing_evidence','wait_for')}
        p['packet_id']=legacy['input']['packet_id']
        validate_proposal(p,legacy,self.now)
        with self.assertRaises(ValueError):validate_proposal(p,self.envelope,self.now)

    def test_rejects_fake_evidence_and_direction_change(self):
        for ref in ('/market/missing','/setup_memory/previous_reviews','/news/-1'):
            p=copy.deepcopy(self.proposal);p['supporting_evidence'][0]['ref']=ref
            with self.assertRaises(ValueError):validate_proposal(p,self.envelope,self.now)
        p={**self.proposal,'action':'SHORT'}
        with self.assertRaises(ValueError):validate_proposal(p,self.envelope,self.now)
        p={**self.proposal,'supporting_evidence':[]}
        with self.assertRaises(ValueError):validate_proposal(p,self.envelope,self.now)

    def test_wait_requires_condition_but_never_permits_execution(self):
        p={**self.proposal,'action':'WAIT'}
        with self.assertRaises(ValueError):validate_proposal(p,self.envelope,self.now)
        p['wait_for']='A later closed candle confirms the retest.'
        self.assertEqual(validate_proposal(p,self.envelope,self.now)['signal']['action'],'WAIT')

    def test_memory_is_versioned_causal_and_never_reuses_an_order(self):
        self.memory.record(self.envelope,{'status':'VALIDATED','proposal':self.proposal},self.now)
        self.assertEqual(self.memory.context(self.market,self.now)['previous_reviews'],[])
        later=self.now+timedelta(minutes=1)
        context=SetupMemory(self.path,'v1').context(self.market,later)
        self.assertEqual(context['previous_reviews'][0]['action'],'LONG')
        self.assertEqual(SetupMemory(self.path,'v2').context(self.market,later)['previous_reviews'],[])
        changed=copy.deepcopy(self.market);changed['metrics']['structure']['bos_time']=124
        self.assertEqual(self.memory.context(changed,later)['previous_reviews'],[])
        self.assertEqual(make_packet(self.market,[],[],later,memory=context)['input']['execution'],'DISABLED')

    def test_reference_outcome_is_not_a_trade_win_and_future_is_hidden(self):
        path=self.path.parent/'signal-history.sqlite3'
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE tracked_signals(id TEXT,data TEXT)')
            db.execute('INSERT INTO tracked_signals VALUES (?,?)',('ref',json.dumps({
                'status':'ACTIVE','stage':1,'model':'MARK_1M_REFERENCE_TOUCH_V1','actual_trade_pnl':'NOT_COMPUTED',
                'issued_ms':self.now.timestamp()*1000,'next_ms':(self.now.timestamp()+60)*1000})))
        self.assertIsNone(self.memory.reference('ref',self.now.timestamp()))
        result=self.memory.reference('ref',self.now.timestamp()+61)
        self.assertEqual(result['status'],'ACTIVE')
        self.assertEqual(result['actual_trade_pnl'],'NOT_COMPUTED')

    def test_402_persists_cooldown_and_new_key_allows_recheck(self):
        clock=Mock(return_value=100)
        config={'AI_API_KEY':'secret-one','AI_BASE_URL':'https://api.apinex.bond/v1','AI_MODEL':'model'}
        health=ProviderHealth(self.path,clock)
        opener=Mock(side_effect=HTTPError('url',402,'secret-one',{},io.BytesIO(b'secret-one')))
        ai=AIClient(config,opener,health)
        with self.assertRaises(AIError):ai.request('test','test')
        with self.assertRaises(AIBackoff):AIClient(config,opener,ProviderHealth(self.path,clock)).request('test','test')
        opener.assert_called_once()
        self.assertEqual(health.status(config)['reason'],'PAYMENT_OR_ACCESS_REQUIRED')
        self.assertNotIn(b'secret-one',self.path.read_bytes())
        claim,blocked=health.claim({**config,'AI_API_KEY':'new-key'})
        self.assertIsNone(blocked)
        health.finish(claim)

    def test_success_usage_recovery_and_single_flight(self):
        clock=Mock(return_value=100);health=ProviderHealth(self.path,clock)
        config={'AI_API_KEY':'key','AI_MODEL':'model'}
        claim,_=health.claim(config)
        self.assertEqual(health.claim(config)[1]['reason'],'REQUEST_IN_PROGRESS')
        health.finish(claim,AIError('rate',429))
        self.assertEqual(health.status(config)['retry_at'],160)
        clock.return_value=161
        claim,blocked=health.claim(config);self.assertIsNone(blocked)
        health.finish(claim,usage={'total_tokens':12})
        self.assertEqual(health.status(config)['reason'],'HEALTHY')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(json.loads(db.execute('SELECT usage FROM provider_usage ORDER BY id DESC LIMIT 1').fetchone()[0]),{'total_tokens':12})

    def test_dead_request_lease_expires(self):
        clock=Mock(return_value=100);health=ProviderHealth(self.path,clock)
        config={'AI_API_KEY':'key','AI_MODEL':'model'}
        old,_=health.claim(config);clock.return_value=191
        new,blocked=health.claim(config);self.assertIsNone(blocked)
        health.finish(old,AIError('old failure',402))
        self.assertEqual(health.status(config)['lease_until'],281)
        health.finish(new)
