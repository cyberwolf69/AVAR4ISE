import copy
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from hedging_tools.pending_setups import PendingSetups, technical_proposal
from hedging_tools.provider_health import ProviderHealth
from hedging_tools.ai_client import AIError
from hedging_tools.analyst import make_packet
from hedging_tools.alerts import notify_signals
from hedging_tools.portfolio import Portfolio


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'db'
        self.now=datetime.now(timezone.utc)
        self.market={'pair':'BTC-USDT-SWAP','status':'WATCHLIST','observed_at':self.now.isoformat(),
                     'candidate':{'setup_model':'structure_bos_retest_v1','action':'LONG','entry':'100','stop_reference':'99'}}
        self.envelope=make_packet(self.market,[],[],self.now,memory={})

    def test_retention_survives_restart_without_extending_expiry(self):
        q=PendingSetups(self.path);q.retain('BTC-USDT-SWAP',100)
        q.retain('BTC-USDT-SWAP',200)
        self.assertEqual(PendingSetups(self.path).pairs(99),['BTC-USDT-SWAP'])
        self.assertEqual(q.pairs(100),[])
        q.retain('ETH-USDT-SWAP',200);q.remove('ETH-USDT-SWAP')
        self.assertEqual(q.pairs(101),[])

    def test_402_retry_budget_and_recovery(self):
        clock=Mock(return_value=100);h=ProviderHealth(self.path,clock);config={}
        for delay in (15,30,60,900):
            claim,blocked=h.claim(config);self.assertIsNone(blocked)
            h.finish(claim,AIError('HTTP 402',402))
            self.assertEqual(h.status(config)['retry_at'],clock.return_value+delay)
            self.assertIsNotNone(h.claim(config)[1])
            clock.return_value+=delay
        claim,_=h.claim(config);h.finish(claim)
        self.assertEqual(h.status(config)['failures'],0)
        claim,_=h.claim(config);h.finish(claim,AIError('HTTP 402',402))
        self.assertEqual(h.status(config)['retry_at'],clock.return_value+15)

    def test_stale_broken_unknown_and_bad_geometry_cannot_publish(self):
        technical_proposal(self.envelope,self.now)
        with self.assertRaises(ValueError):technical_proposal(self.envelope,self.now+timedelta(seconds=181))
        for change in ({'status':'WAIT'}, {'candidate':{**self.market['candidate'],'stop_reference':'101'}},
                       {'candidate':{**self.market['candidate'],'setup_model':'unknown'}}):
            e=copy.deepcopy(self.envelope);e['input']['market'].update(change)
            with self.assertRaises(ValueError):technical_proposal(e,self.now)

    def test_technical_alert_is_not_ai_and_cannot_enter(self):
        analysis={'status':'ERROR','reason':'Provider unavailable','publication_basis':'TECHNICAL',
                  'technical_proposal':technical_proposal(self.envelope,self.now)}
        with patch('hedging_tools.alerts.settings',return_value=[{'channel':'discord'}]), \
             patch('hedging_tools.alerts.deliver',return_value=[]) as deliver:
            notify_signals([self.envelope],[analysis])
            card=deliver.call_args.args[1]['card']
            self.assertIn('Technical Rationale',[f['name'] for f in card['fields']])
        manager=Mock()
        result=Portfolio._enter(manager,self.envelope,analysis,{})
        self.assertEqual(result['status'],'WAIT')
        manager.manage.assert_not_called()
