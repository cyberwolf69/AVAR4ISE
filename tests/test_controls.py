import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
from hedging_tools.controls import Controls,DEFAULT,validate
from hedging_tools.telegram_remote import handle,report,settings,API,CommandMenu,COMMANDS
from hedging_tools.portfolio import Portfolio,entry_allowance
from hedging_tools.demo_execution import plan
from tests import test_demo_execution


class ControlTests(unittest.TestCase):
    def test_menu_registered_for_owner_once(self):
        api=Mock();api.call.return_value=True
        menu=CommandMenu()
        self.assertEqual(menu.tick(api,123,0)['status'],'COMMAND_MENU_READY')
        self.assertEqual(api.call.call_args_list[0].args,('setMyCommands',{
            'commands':COMMANDS,'scope':{'type':'chat','chat_id':123},'language_code':''}))
        self.assertEqual(api.call.call_args_list[1].args,('setChatMenuButton',{
            'chat_id':123,'menu_button':{'type':'commands'}}))
        self.assertIsNone(menu.tick(api,123,120))
        self.assertEqual(api.call.call_count,2)

    def test_menu_failure_is_sanitized_and_retried(self):
        api=Mock();api.call.side_effect=[True,RuntimeError('secret-token'),True,True]
        menu=CommandMenu()
        result=menu.tick(api,123,0)
        self.assertEqual(result['status'],'COMMAND_MENU_RETRY')
        self.assertNotIn('secret-token',str(result))
        self.assertFalse(menu.ready)
        self.assertIsNone(menu.tick(api,123,59))
        self.assertEqual(api.call.call_count,2)
        self.assertEqual(menu.tick(api,123,60)['status'],'COMMAND_MENU_READY')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.controls=Controls(self.root/'controls.sqlite3')
        self.now=1000

    def tearDown(self):
        self.temp.cleanup()

    def message(self,text,owner=123,chat=123,kind='private',date=1000):
        return {'update_id':1,'message':{'from':{'id':owner},'chat':{'id':chat,'type':kind},'text':text,'date':date}}

    def test_defaults_and_confirmation_persistence(self):
        self.assertEqual(self.controls.read(),DEFAULT)
        token,before,after=self.controls.propose(123,{'max_leverage':20,'margin':'3'},1000)
        self.assertEqual(self.controls.read(),DEFAULT)
        self.controls.confirm(123,token,1001)
        restarted=Controls(self.controls.path)
        self.assertEqual(restarted.read()['max_leverage'],20)
        self.assertEqual(restarted.read()['margin'],'3')
        with self.assertRaises(ValueError):
            restarted.confirm(123,token,1002)

    def test_bounds_cannot_be_overridden(self):
        for change in ({'margin':'6'},{'margin':'NaN'},{'min_leverage':9},{'max_leverage':31},{'max_positions':6},{'max_positions':True}):
            with self.assertRaises(ValueError):
                self.controls.propose(123,change,1000)

    def test_owner_expiry_and_old_proposal(self):
        token,_,_=self.controls.propose(123,{'margin':'2'},1000)
        with self.assertRaises(ValueError):self.controls.confirm(456,token,1001)
        with self.assertRaises(ValueError):self.controls.confirm(123,token,1120)
        token,_,_=self.controls.propose(123,{'margin':'2'},1000)
        newer,_,_=self.controls.propose(123,{'margin':'3'},1000)
        with self.assertRaises(ValueError):self.controls.confirm(123,token,1001)
        self.controls.confirm(123,newer,1001)

    def test_foreign_group_forward_old_messages_ignored(self):
        for msg in (self.message('/pause',owner=456),self.message('/pause',kind='group'),
                    self.message('/pause',chat=456),self.message('/pause',date=0)):
            self.assertIsNone(handle(msg,123,self.controls,self.root,1000))
        msg=self.message('/pause');msg['message']['forward_origin']={}
        self.assertIsNone(handle(msg,123,self.controls,self.root,1000))
        self.assertFalse(self.controls.path.exists())

    def test_pause_requires_confirmation_and_blocks_entry(self):
        reply=handle(self.message('/pause'),123,self.controls,self.root,1000)
        self.assertTrue(self.controls.read()['entries_enabled'])
        token=reply.split('/confirm ')[1]
        handle(self.message('/confirm '+token),123,self.controls,self.root,1001)
        manager=Portfolio(Mock(),Mock(),Mock(),controls=self.controls)
        self.assertEqual(manager.enter({}, {})['reason'],'ENTRIES_PAUSED_BY_OWNER')
        manager.client.post.assert_not_called()
        manager.store.read.assert_not_called()

    def test_lowered_position_cap_blocks_new_entry(self):
        state={'halted':False,'positions':[{'pair':'BTC-USDT-SWAP','status':'OPEN'}]}
        self.assertEqual(entry_allowance(state,'ETH-USDT-SWAP',[],[],1)[1],'POSITION_SLOTS_FULL')
        self.assertEqual(state['positions'][0]['status'],'OPEN')

    def test_cursor_is_monotonic(self):
        self.controls.initialize();self.controls.acknowledge(12);self.controls.acknowledge(4)
        self.assertEqual(self.controls.offset(),13)

    def test_config_missing_owner_fails_closed(self):
        p=self.root/'.env';p.write_text('TELEGRAM_REMOTE_ENABLED=true\nTELEGRAM_BOT_TOKEN=123:dummy\n')
        with self.assertRaises(ValueError):settings(p)
        p.write_text(p.read_text()+'TELEGRAM_OWNER_ID=123\n')
        self.assertEqual(settings(p)[0],123)

    def test_unknown_heartbeat_not_running(self):
        text=report('/status',self.controls,self.root,1000)
        self.assertIn('UNKNOWN',text)
        self.assertIn('N/A',text)

    def test_error_does_not_expose_token(self):
        api=API('123:SECRET');api.opener=Mock(side_effect=OSError(api.base))
        with self.assertRaises(RuntimeError) as e:api.call('getUpdates',{})
        self.assertNotIn('SECRET',str(e.exception))

    def test_actual_performance_excludes_unfilled_and_shadow(self):
        state={'positions':[{'status':'CLOSED','pnl':'1','exit_kind':'TP3'},
                            {'status':'CLOSED','pnl':'0','exit_kind':'NO_FILL'}],
               'funding':{'42':'-.1'},'equity':'50.9'}
        with closing(sqlite3.connect(self.root/'portfolio.sqlite3')) as db,db:
            db.execute('CREATE TABLE portfolio(id INTEGER,data TEXT)')
            db.execute('INSERT INTO portfolio VALUES (1,?)',(json.dumps(state),))
        text=report('/performance',self.controls,self.root,1000)
        self.assertIn('Closed positions: 1',text)
        self.assertIn('100.0%',text)
        self.assertIn('Net realized + funding: 0.9',text)

    def test_custom_leverage_margin_and_risk(self):
        fixture=test_demo_execution.ExecutionTests();fixture.setUp()
        signal={'pair':'BTC-USDT-SWAP','action':'LONG','stop':'79920'}
        r=plan(fixture.instrument,fixture.ticker,signal,margin='2',min_leverage=10,max_leverage=15)
        self.assertEqual(r['leverage'],'15')
        self.assertLessEqual(float(r['margin_upper_bound']),2)
        self.assertLessEqual(float(r['planned_risk']),1)
        with self.assertRaises(ValueError):plan(fixture.instrument,fixture.ticker,signal,max_leverage=31)
