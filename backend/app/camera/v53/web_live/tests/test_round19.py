import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from web_live.collection import FrameCollection
from web_live.live import LiveSession
from web_live.policy import parking_rule_status
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'data'/'collection.db'
        self.collection=FrameCollection(self.path)
        self.live=LiveSession(Pipeline(),discover_fn=lambda:[{},{}],hub_factory=Hub,
                              transport_factory=Transport,collection=self.collection)
        self.addCleanup(self.live.close)

    def test_two_channels_keep_collecting_without_browser_or_network(self):
        self.live.start()
        self.live.last_client=time.monotonic()-35
        self.live.sending=True
        for c in ('1','2'):self.live.hub.feed(c)
        def records():
            return self.collection.snapshot({},True)['channels']
        self.assertTrue(eventually(lambda:all(e['saved']==1 for e in records().values())))
        self.assertTrue(self.live.running)
        self.assertFalse(self.live.sending)
        first=records()['1']
        self.live.hub.packets.pop('1')
        self.live.hub.feed('2',seq=2)
        self.assertTrue(eventually(lambda:records()['2']['saved']==2))
        state=self.live.snapshot(False,collected=True,seen={'1':first['key']})
        self.assertNotIn('1',state['results'])
        self.assertEqual(state['collection']['channels']['1']['key'],first['key'])
        self.assertNotIn('result',state['collection']['channels']['1'])
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('select channel,sequence,payload from observations order by channel,sequence').fetchall()
            self.assertEqual([(c,s) for c,s,_ in rows],[('1',1),('2',1),('2',2)])
            self.assertTrue(all('spaces' in json.loads(data) for _,_,data in rows))

    def test_failed_write_recovery_and_new_session(self):
        self.collection.begin()
        sample={'stream_epoch':1,'frame_sequence':2,'observed_at':'2026-11-09T00:00:00Z','annotated_image':'test'}
        def channel():
            return self.collection.snapshot({},True)['channels']['1']
        with patch.object(self.collection,'_connect',side_effect=sqlite3.OperationalError('busy')):
            with self.assertLogs('web_live.collection',level='ERROR'):
                self.collection.offer('1',sample,True)
                self.assertTrue(eventually(lambda:channel()['failed']==1))
        self.assertIsNone(channel()['key'])
        self.collection.offer('1',sample,False)
        self.assertTrue(eventually(lambda:channel()['saved']==1))
        self.collection.offer('1',sample,False)
        old=channel()['key']
        self.collection.begin()
        self.collection.offer('1',sample,True)
        self.assertTrue(eventually(lambda:channel()['saved']==1))
        self.assertNotEqual(channel()['key'],old)
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('select timely,payload from observations order by rowid').fetchall()
            self.assertEqual([r[0] for r in rows],[0,1])
            self.assertTrue(all('annotated_image' not in json.loads(p) for _,p in rows))


class ParkingRulesTests(unittest.TestCase):
    def test_all_nine_pairs_and_insufficient_evidence(self):
        base=dict(confidence=.8,sendable=True,observation='occupied',car_id=None)
        allowed={'general':{'general','ev','accessible'},'ev':{'ev'},'accessible':{'accessible'}}
        for zone in allowed:
            for kind in ('general','ev','accessible'):
                row=dict(base,zone_type=zone,vehicle_type=kind)
                self.assertEqual(parking_rule_status(row,True),'matched' if kind in allowed[zone] else 'mismatch')
                self.assertEqual(parking_rule_status(row,False),'unverified')
        for patch in ({'confidence':.79},{'confidence':None},{'sendable':False},{'vehicle_type':None}):
            self.assertEqual(parking_rule_status(row|patch,True),'unverified')
        for observation in ('empty','unknown'):
            self.assertIsNone(parking_rule_status(row|{'observation':observation},True))
