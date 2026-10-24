import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from hedging_tools.alerts import signal_message, deliver, AlertError, payload
from hedging_tools.analyst import make_packet
from hedging_tools.card_updates import edit, progress, render, update


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        now=datetime.now(timezone.utc)
        envelope=make_packet({'pair':'BTC-USDT-SWAP','status':'WATCHLIST',
            'candidate':{'action':'LONG','entry':'100','stop_reference':'99'}},[],[],now)
        proposal=dict(packet_id=envelope['input']['packet_id'],pair='BTC-USDT-SWAP',action='LONG',
            reason='Example',invalidation='Support fails',source_ids=[],expires_at=envelope['input']['expires_at'])
        self.message=signal_message(envelope,proposal,now)
        self.ref=dict(id=self.message['id'],status='ACTIVE',stage=1,entry='100',current_stop='100.2',
                      targets=['101','102','103'],quality='OK',events=[{'event':'TP1'}])
        self.discord={'channel':'discord','url':'https://discord.com/api/webhooks/123/secret?wait=true'}
        self.telegram={'channel':'telegram','url':'https://api.telegram.org/bot123:secret/sendMessage','chat_id':'123'}
        self.position=dict(signal_id=self.message['id'],status='OPEN',direction='LONG',entry='100',stop='99',
            current_stop='100.2',legs=[{'price':str(101+i),'size':s} for i,s in enumerate(['.4','.35','.25'])],
            execution_evidence=[dict(role='TP1',state='filled',accFillSz='.4')])

    def save_reference(self):
        with closing(sqlite3.connect(self.root/'signal-history.sqlite3')) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS tracked_signals(data TEXT)')
            db.execute('DELETE FROM tracked_signals')
            db.execute('INSERT INTO tracked_signals VALUES (?)',(json.dumps(self.ref),))

    def test_initial_markers_and_reference_progress(self):
        self.assertEqual([f['name'] for f in self.message['card']['fields'][:5]],
                         ['Entry','⬜ Stop Loss','⬜ TP1','⬜ TP2','⬜ TP3'])
        card=render(self.message,progress(self.ref))['card']
        self.assertEqual(card['fields'][2]['name'],'✅ TP1 HIT')
        self.assertEqual(card['fields'][3]['name'],'⬜ TP2')
        self.assertIn('Reference price touches',card['subtitle'])
        self.assertEqual(self.message['card']['fields'][2]['name'],'⬜ TP1')
        self.ref.update(status='PROTECTED_STOP')
        self.assertEqual(render(self.message,progress(self.ref))['card']['fields'][1]['name'],'✅ BEP HIT')
        self.ref.update(stage=2,current_stop='101',events=[{'event':'TP1'},{'event':'TP2'}])
        self.assertEqual(render(self.message,progress(self.ref))['card']['fields'][1]['name'],'✅ PROFIT STOP HIT')

    def test_ambiguous_or_gap_does_not_invent_hits(self):
        self.ref.update(status='AMBIGUOUS',stage=0,events=[],quality='DATA_GAP')
        msg=render(self.message,progress(self.ref))
        self.assertFalse(any('✅' in f['name'] for f in msg['card']['fields']))
        self.assertIn('DATA GAP',msg['card']['fields'][-1]['value'])

    def test_fills_override_reference_and_partial_is_not_completed(self):
        self.ref.update(stage=3,events=[{'event':f'TP{i}'} for i in (1,2,3)])
        self.position['execution_evidence'][0]['accFillSz']='.2'
        p=progress(self.ref,self.position)
        self.assertEqual(p['hits'],[False,False,False])
        self.assertEqual(p['scope'],'OKX confirmed fills')
        self.position['execution_evidence'][0]['accFillSz']='.4'
        self.assertEqual(progress(self.ref,self.position)['hits'],[True,False,False])
        self.position['execution_evidence'].append(dict(role='SL',state='filled',accFillSz='.6'))
        self.assertTrue(progress(self.ref,self.position)['protected'])
        self.assertEqual(progress(self.ref,self.position,True)['quality'],'RECONCILIATION_PENDING')

    def test_short_stop_and_expired_state(self):
        self.position.update(direction='SHORT',current_stop='99.8')
        self.position['execution_evidence'].append(dict(role='SL',state='filled',accFillSz='.6'))
        self.assertTrue(progress(self.ref,self.position)['protected'])
        self.ref.update(status='EXPIRED',stage=0,events=[])
        self.assertIn('EXPIRED',render(self.message,progress(self.ref))['card']['fields'][-1]['value'])

    def test_edit_endpoints_and_receipts(self):
        opener=Mock(return_value=io.BytesIO(b'{"id":"42"}'))
        edit(self.discord,'42',self.message,opener)
        req=opener.call_args.args[0]
        self.assertEqual(req.method,'PATCH')
        self.assertTrue(req.full_url.endswith('/messages/42'))
        self.assertEqual(json.loads(req.data)['allowed_mentions'],{'parse':[]})
        opener=Mock(return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":42}}'))
        edit(self.telegram,'42',self.message,opener)
        req=opener.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/editMessageText'))
        self.assertEqual(json.loads(req.data)['message_id'],42)
        with self.assertRaises(AlertError):
            edit(self.discord,'42',self.message,Mock(return_value=io.BytesIO(b'{"id":"99"}')))

    def test_not_modified_is_success_and_errors_are_redacted(self):
        exc=HTTPError('secret',400,'secret',{},io.BytesIO(b'{"description":"Bad Request: message is not modified"}'))
        edit(self.telegram,'42',self.message,Mock(side_effect=exc))
        with self.assertRaises(AlertError) as caught:
            edit(self.discord,'42',self.message,Mock(side_effect=OSError('secret')))
        self.assertNotIn('secret',str(caught.exception))

    def test_durable_receipts_restart_dedup_and_target_isolation(self):
        deliver([self.discord,self.telegram],self.message,self.root/'alerts.sqlite3',Mock(return_value='42'))
        self.save_reference()
        sender=Mock()
        self.assertEqual(update(self.root,[self.discord,self.telegram],sender,100)['updated'],2)
        self.assertEqual(update(self.root,[self.discord,self.telegram],sender,101)['updated'],0)
        self.ref.update(stage=2,events=[{'event':'TP1'},{'event':'TP2'}]);self.save_reference()
        self.assertEqual(update(self.root,[self.discord],sender,102)['updated'],1)
        changed={**self.telegram,'chat_id':'999'}
        self.assertEqual(update(self.root,[changed],sender,103)['updated'],0)
        self.assertNotIn(b'secret',(self.root/'alerts.sqlite3').read_bytes())

    def test_retry_is_edit_only_and_missing_message_disabled(self):
        deliver([self.discord],self.message,self.root/'alerts.sqlite3',Mock(return_value='42'))
        self.save_reference()
        sender=Mock(side_effect=TimeoutError())
        self.assertEqual(len(update(self.root,[self.discord],sender,100)['errors']),1)
        update(self.root,[self.discord],sender,110);sender.assert_called_once()
        sender=Mock(side_effect=AlertError('Missing',404))
        update(self.root,[self.discord],sender,161)
        update(self.root,[self.discord],sender,10000);sender.assert_called_once()

    def test_old_unconfirmed_messages_are_not_resent(self):
        deliver([self.discord],self.message,self.root/'alerts.sqlite3',Mock(side_effect=TimeoutError()))
        self.save_reference();sender=Mock()
        self.assertEqual(update(self.root,[self.discord],sender,100)['updated'],0)
        sender.assert_not_called()

    def test_actual_position_selected_over_reference(self):
        deliver([self.discord],self.message,self.root/'alerts.sqlite3',Mock(return_value='42'))
        self.save_reference()
        with closing(sqlite3.connect(self.root/'portfolio.sqlite3')) as db,db:
            db.execute('CREATE TABLE portfolio(id INTEGER,data TEXT)')
            db.execute('INSERT INTO portfolio VALUES(1,?)',(json.dumps({'positions':[self.position]}),))
        sender=Mock();update(self.root,[self.discord],sender,100)
        self.assertIn('OKX confirmed fills',sender.call_args.args[2]['card']['subtitle'])
        self.assertLess(len(payload(self.telegram,sender.call_args.args[2])['text']),4096)
