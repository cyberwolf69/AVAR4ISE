import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from hedging_tools.engine_events import EngineEvents


class EventTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        env=root/'.env';env.write_text('TELEGRAM_BOT_TOKEN=123:SECRET\n')
        self.events=EngineEvents(root/'events.sqlite3',env)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self):
        with closing(sqlite3.connect(self.events.path)) as db:
            return db.execute('SELECT text,status FROM events').fetchall()

    def test_dedup_redaction_and_recovery(self):
        self.events.emit('ERROR','123:SECRET https://example.org/secret',now=0)
        self.events.emit('ERROR','123:SECRET https://example.org/secret',now=10)
        self.events.emit('ERROR','Recovered',now=20)
        self.assertEqual(len(self.rows()),2)
        self.assertNotIn('SECRET',str(self.rows()))
        self.assertNotIn('example.org',str(self.rows()))

    def test_ai_errors_survive_wait_decisions_and_no_call_is_not_recovery(self):
        self.events.report({'status':'SCAN_COMPLETE','cycle':1,'decisions':[{'status':'WAIT'}],
                            'ai':{'errors':[{'pair':'BTC','reason':'AI provider HTTP 402'}]}})
        self.assertIn('Payment Required',str(self.rows()))
        self.events.report({'status':'SCAN_COMPLETE','cycle':2,'decisions':[],'ai':{'validated':0}})
        self.assertNotIn('validated successfully',str(self.rows()))
        self.events.report({'status':'SCAN_COMPLETE','cycle':3,'decisions':[],'ai':{'validated':1}})
        self.assertIn('validated successfully',str(self.rows()))

    def test_delivery_only_to_owner_and_uncertain_delivery_not_replayed(self):
        self.events.emit('TEST','one')
        api=Mock();api.call.side_effect=RuntimeError('timeout')
        with self.assertRaises(RuntimeError):self.events.drain(api,123)
        self.assertEqual(api.call.call_args.args[1]['chat_id'],123)
        self.events.drain(api,123)
        api.call.assert_called_once()
        self.assertEqual(self.rows()[0][1],'UNCONFIRMED')

    def test_pending_survives_restart_and_success_is_recorded(self):
        self.events.emit('ENGINE_STOPPED','Stopped')
        restarted=EngineEvents(self.events.path,Path(self.temp.name)/'.env')
        api=Mock();api.call.return_value={'message_id':1}
        restarted.drain(api,123)
        self.assertEqual(self.rows()[0][1],'SENT')

    def test_positions_only_publish_changes(self):
        state={'positions':[{'id':'a','pair':'BTC','status':'OPEN','stop':'10'}]}
        self.events.positions(state);self.events.positions(state)
        self.assertEqual(len(self.rows()),1)
        state['positions'][0]['stop_history']=[{'to':'11'}]
        self.events.positions(state)
        self.assertEqual(len(self.rows()),2)
