"""Faults reproduced in the 2026-11-07 review, without camera hardware."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from web_live.number_reader import NumberReader, signature
from web_live.pipeline import ParkingPipeline
from web_live.policy import without_duplicate_identities, to_events, confirmed_number
from web_live.transport import EventTransport
from web_live.tests.test_contract import fixture


class ReviewRegressions(unittest.TestCase):
    def test_unseen_number_is_not_vetoed_by_closed_six_class_classifier(self):
        classes={'11','28','34','49','77','87'}
        for number in ('23','42','57','85'):
            self.assertEqual(confirmed_number({'number':number,'confidence':.95},'11','ev',model_classes=classes),number)
        self.assertIsNone(confirmed_number({'number':'87','confidence':.95},'34','general',model_classes=classes))
        self.assertIsNone(confirmed_number({'number':'42','confidence':.95},'11','accessible',model_classes=classes))
        self.assertIsNone(confirmed_number({'number':'42','confidence':.79},'11','ev',model_classes=classes))

    def test_duplicate_number_cleared_in_rows_detections_and_events(self):
        original={}; packets={}
        for camera,lot in [('1','A'),('2','B')]:
            r,packets[camera]=fixture(lot)
            r['spaces'][0].update(car_id='11',vehicle_type='ev',observation='occupied')
            r['detections']=[{'space_id':'A-01','car_id':'11','number_candidate':'11'}]
            original[camera]=r
        normalized=without_duplicate_identities(original)
        for camera,lot in [('1','A'),('2','B')]:
            r=normalized[camera]
            self.assertIsNone(r['spaces'][0]['car_id'])
            self.assertIsNone(r['detections'][0]['car_id'])
            self.assertIsNone(to_events(r,packets[camera],'camera_'+lot.lower())[0]['car_id'])
            self.assertEqual(r['detections'][0]['number_candidate'],'11')
            self.assertEqual(original[camera]['detections'][0]['car_id'],'11')

    def test_one_camera_invalidation_preserves_other_cache_and_pending_jobs(self):
        reader=NumberReader(); reader.worker=SimpleNamespace(is_alive=lambda:True)
        crop=np.full((40,40,3),128,np.uint8)
        for camera in ('1','2'):
            key=(camera,1,'A-01')
            reader.query(key,crop)
            reader.cache[key]=(time.monotonic(),signature(crop),{'number':'23','confidence':.95})
        old_1=reader.pending[('1',1,'A-01')]; old_2=reader.pending[('2',1,'A-01')]
        pipeline=ParkingPipeline.__new__(ParkingPipeline)
        pipeline.lock=threading.RLock(); pipeline.numbers=reader
        pipeline.baselines={'1':{},'2':{}};pipeline.confirmed={'1':{},'2':{}};pipeline.search_order={}
        pipeline.invalidate('1')
        self.assertEqual(set(reader.cache),{('2',1,'A-01')})
        self.assertEqual(set(reader.pending),{('2',1,'A-01')})
        self.assertEqual(reader.jobs.qsize(),1)
        self.assertNotEqual(old_1,reader._generation(('1',1,'A-01')))
        self.assertEqual(old_2,reader._generation(('2',1,'A-01')))
        self.assertIn('2',pipeline.confirmed)
        reader.invalidate()
        self.assertEqual(reader.cache,{})
        self.assertEqual(reader.jobs.qsize(),0)
        self.assertNotEqual(old_2,reader._generation(('2',1,'A-01')))

    def test_28_bays_survive_400ms_receiver_and_900ms_initial_age(self):
        accepted=[]; lock=threading.Lock()
        class Response:
            status=202
            length=None
            def __init__(self,event_id):self.event_id=event_id
            def read(self,limit):return json.dumps(dict(event_id=self.event_id,disposition='accepted')).encode()
            def __enter__(self):return self
            def __exit__(self,*args):return False
        class Opener:
            def open(self,request,timeout):
                event=json.loads(request.data)
                time.sleep(.4)
                with lock:accepted.append((event['lot_id'],event['space_id']))
                return Response(event['event_id'])
        with patch.dict('os.environ',API_BASE_URL='http://127.0.0.1:8000',VISION_TOKEN='mock-only'), \
             patch('web_live.transport.urllib.request.build_opener',return_value=Opener()):
            transport=EventTransport()
            try:
                for batch in range(2):
                    events=[]
                    for lot in ('A','B'):
                        r,p=fixture(lot);events.extend(to_events(r,p,'camera_'+lot.lower()))
                    stamp=(datetime.now(timezone.utc)-timedelta(seconds=.9)).isoformat()
                    for event in events:event['observed_at']=stamp
                    transport.offer(events,lambda _:True)
                    deadline=time.monotonic()+5
                    while sum(transport.snapshot()['counts'].values())<28*(batch+1):
                        self.assertLess(time.monotonic(),deadline)
                        time.sleep(.01)
                self.assertEqual(transport.snapshot()['counts'],{'accepted':56})
                self.assertEqual(sum(lot=='A' for lot,_ in accepted),24)
                self.assertEqual(sum(lot=='B' for lot,_ in accepted),32)
            finally:transport.close()

    def test_bad_event_and_validator_error_do_not_kill_sender(self):
        transport=EventTransport()
        try:
            r,p=fixture();event=to_events(r,p,'camera_a')[0]
            bad=dict(event,bbox_xyxy=object())
            self.assertEqual(transport.send_one(bad,lambda _:True,'http://127.0.0.1','mock',transport.generation),'invalid_event')
            def broken(_):raise RuntimeError('validator failure')
            with patch.dict('os.environ',API_BASE_URL='http://127.0.0.1:8000',VISION_TOKEN='mock-only'):
                transport.offer([event],broken)
                deadline=time.monotonic()+2
                while not transport.snapshot()['counts']:
                    self.assertLess(time.monotonic(),deadline);time.sleep(.01)
            self.assertEqual(transport.snapshot()['counts'],{'delivery_error':1})
            self.assertTrue(transport.snapshot()['worker_alive'])
        finally:transport.close()


if __name__=='__main__':unittest.main()
