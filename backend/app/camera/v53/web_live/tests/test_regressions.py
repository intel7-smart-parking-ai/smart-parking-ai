import copy
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
import numpy as np
from PIL import Image
from web_live.number_reader import NumberReader
from web_live.number_reader import signature
from web_live.policy import without_duplicate_identities,to_events
from web_live.evaluate import evaluate
from web_live.transport import EventTransport,destination
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub,Pipeline,Transport,eventually
from web_live.live import LiveSession


class RegressionTests(unittest.TestCase):
    def test_both_camera_duplicate_identity_becomes_null(self):
        a,_=fixture('A');b,_=fixture('B')
        for r in (a,b):r['spaces'][0].update(car_id='11',observation='occupied',vehicle_type='ev')
        out=without_duplicate_identities({'1':a,'2':b})
        self.assertIsNone(out['1']['spaces'][0]['car_id']);self.assertIsNone(out['2']['spaces'][0]['car_id'])
        self.assertEqual(a['spaces'][0]['car_id'],'11')
        self.assertTrue(out['1']['spaces'][0]['sendable'])

    def test_ocr_queue_does_not_starve_later_bays(self):
        r=NumberReader();r.worker=SimpleNamespace(is_alive=lambda:True)
        for i in range(28):r.query(('camera',i),np.full((40,40,3),128,np.uint8))
        self.assertEqual(len(r.pending),28);self.assertEqual(r.jobs.qsize(),28)
        for i in range(28):r.query(('camera',i),np.full((40,40,3),128,np.uint8))
        self.assertEqual(r.jobs.qsize(),28)
        r.invalidate();self.assertEqual(r.pending,{});self.assertEqual(r.jobs.qsize(),0)

    def test_ocr_refresh_keeps_only_current_unexpired_identity(self):
        r=NumberReader();r.worker=SimpleNamespace(is_alive=lambda:True)
        crop=np.full((40,40,3),128,np.uint8);key=('camera','A-01');answer={'number':'23','confidence':.95}
        r.cache[key]=(100.,signature(crop),answer)
        with patch('web_live.number_reader.time.monotonic',return_value=102.5):
            self.assertEqual(r.query(key,crop),answer);self.assertEqual(r.jobs.qsize(),1)
        changed=crop.copy();changed[10:30,10:20]=0
        with patch('web_live.number_reader.time.monotonic',return_value=102.6):self.assertIsNone(r.query(key,changed))
        r.cache[key]=(100.,signature(crop),answer)
        with patch('web_live.number_reader.time.monotonic',return_value=104.1):self.assertIsNone(r.query(key,crop))

    def test_old_epoch_queued_event_rejected_after_reconnect(self):
        live=LiveSession(Pipeline(),discover_fn=lambda:[{}],hub_factory=Hub,transport_factory=Transport)
        try:
            live.start();live.hub.feed(epoch=3);self.assertTrue(eventually(lambda:bool(live.results)));live.sending=True
            event={'lot_id':'A','space_id':'A-01','event_type':'space_empty_observed','frame_id':'camera_a:2:1:old'}
            self.assertFalse(live._valid_event(event));event['frame_id']='camera_a:3:1:current'
            self.assertTrue(live._valid_event(event))
        finally:live.close()

    def test_transport_retry_preserves_event_id(self):
        t=EventTransport();r,p=fixture();event=to_events(r,p,'camera_a')[0]
        response=MagicMock();response.__enter__.return_value.status=200
        response.__enter__.return_value.length=None
        response.__enter__.return_value.read.return_value=json.dumps(dict(event_id=event['event_id'],disposition='duplicate')).encode()
        opener=MagicMock();opener.open.side_effect=[urllib.error.URLError('temporary'),response]
        try:
            with patch('urllib.request.build_opener',return_value=opener):
                self.assertEqual(t.send_one(event,lambda _:True,'http://127.0.0.1:8000','test',t.generation),'duplicate')
            requests=[c.args[0] for c in opener.open.call_args_list]
            self.assertEqual(requests[0].data,requests[1].data)
            self.assertEqual(json.loads(requests[1].data)['event_id'],event['event_id'])
        finally:t.close()

    def test_invalid_ports_and_header_newlines(self):
        for url,token in [('http://localhost:no','test'),('http://localhost:65536','test'),('http://localhost:0','test'),('http://localhost','a\nb')]:
            with patch.dict('os.environ',API_BASE_URL=url,VISION_TOKEN=token):self.assertRaises(ValueError,destination)

    def test_invalid_timestamp_is_withheld(self):
        for stamp in (None,123,'invalid'):
            r,p=fixture();r['observed_at']=p['observed_at']=stamp
            self.assertEqual(to_events(r,p,'camera_a'),[])


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        r,_=fixture();r['spaces'][0].update(observation='occupied',car_id=None,vehicle_type='ev')
        Image.new('RGB',(40,35),'white').save(self.base/'image',format='PNG')
        (self.base/'prediction.json').write_text(json.dumps(r))
        truth={s['space_id']:{'occupancy':'empty'} for s in r['spaces']}
        truth['A-01']={'occupancy':'occupied','car_id':'23','vehicle_type':'ev'}
        self.sample=dict(split='test',session_id='session-1',sample_id='one',image='image',prediction='prediction.json',lot_id='A',truth=truth,truth_reviewed_independently=True)
    def tearDown(self):self.tmp.cleanup()
    def test_null_is_counted_separately(self):
        m=evaluate({'samples':[self.sample]},self.base)
        self.assertEqual(m['occupancy'],{'correct':12,'wrong':0,'unknown':0});self.assertEqual(m['number'],{'correct':0,'wrong':0,'null':1})
        self.assertEqual(m['color']['ev']['correct'],1)
    def test_same_session_between_splits_rejected(self):
        other=dict(self.sample,split='train',sample_id='two',image='different')
        Image.new('RGB',(40,35),'blue').save(self.base/'different',format='PNG')
        self.assertRaises(ValueError,evaluate,{'samples':[self.sample,other]},self.base)
    def test_same_image_duplicates_rejected(self):
        other=dict(self.sample,session_id='another',sample_id='two')
        self.assertRaises(ValueError,evaluate,{'samples':[self.sample,other]},self.base)
    def test_non_independent_truth_rejected(self):
        self.sample['truth_reviewed_independently']=False
        self.assertRaises(ValueError,evaluate,{'samples':[self.sample]},self.base)
    def test_bad_truth_rejected(self):
        self.sample['truth']['A-01']['car_id']=23
        self.assertRaises(ValueError,evaluate,{'samples':[self.sample]},self.base)
