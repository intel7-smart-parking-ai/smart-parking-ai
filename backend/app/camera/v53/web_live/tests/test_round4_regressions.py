"""OCR geometry, weak detections, and acknowledgement uncertainty regressions."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from web_live.sticker_geometry import rectify_sticker
from web_live.pipeline import ParkingPipeline
from web_live.transport import EventTransport
from web_live.tests.test_contract import fixture
from web_live.policy import to_events
from web_live.live import LiveSession
from web_live.tests.test_lifecycle import Pipeline,Hub,Transport,eventually


class Round4Regressions(unittest.TestCase):
    def test_pending_review_gets_a_turn_before_next_inference(self):
        pipeline=Pipeline()
        live=LiveSession(pipeline,discover_fn=lambda:[{}],hub_factory=Hub,transport_factory=Transport)
        try:
            live.start()
            with live._review_turn():
                live.hub.feed()
                self.assertFalse(eventually(lambda:bool(live.results),timeout=.12))
            self.assertTrue(eventually(lambda:bool(live.results)))
            self.assertEqual(live.review_waiters,0)
            with self.assertRaises(ValueError):
                with live._review_turn():raise ValueError('review failed')
            self.assertEqual(live.review_waiters,0)
        finally:live.close()

    def test_enabling_delivery_does_not_discard_inflight_inference(self):
        pipeline=Pipeline();entered=threading.Event();release=threading.Event();sequences=[]
        original=pipeline.analyze
        def analyze(image,**kw):
            sequences.append(kw['sequence'])
            if kw['sequence']==2:entered.set();release.wait(2)
            return original(image,**kw)
        pipeline.analyze=analyze
        live=LiveSession(pipeline,discover_fn=lambda:[{}],hub_factory=Hub,transport_factory=Transport)
        try:
            live.start();live.hub.feed(seq=1)
            self.assertTrue(eventually(lambda:bool(live.results)))
            live.hub.feed(seq=2);self.assertTrue(entered.wait(1))
            with patch('web_live.live.destination',return_value=('http://127.0.0.1','test-only')):
                live.enable_send(True,simulator_stopped=True)
            release.set()
            self.assertTrue(eventually(lambda:live.results.get('1',{}).get('frame_sequence')==2))
            self.assertEqual(sequences.count(2),1)
        finally:release.set();live.close()

    def test_separated_same_color_floor_does_not_change_label_geometry(self):
        label=np.zeros((60,70,3),np.uint8)
        cv2.fillConvexPoly(label,np.int32([[30,2],[58,30],[30,58],[2,30]]),(220,100,20))
        floor=label.copy();cv2.rectangle(floor,(60,25),(69,45),(220,100,20),-1)
        transform=cv2.getPerspectiveTransform
        with patch('web_live.sticker_geometry.cv2.getPerspectiveTransform',wraps=transform) as matrix:
            self.assertTrue(rectify_sticker(label,'blue')[1])
            reference=matrix.call_args.args[0].copy()
            self.assertTrue(rectify_sticker(floor,'blue')[1])
            np.testing.assert_allclose(matrix.call_args.args[0],reference)

    def pipeline_result(self,score,reading):
        p=ParkingPipeline.__new__(ParkingPipeline)
        poly=np.float32([[0,0],[100,0],[100,100],[0,100]])
        registration=dict(lot_id='A',outline=poly,matrix=np.eye(3),inliers=30,
                          residual_px=0.,line_support=.9,polygons={'A-01':poly})
        p.registry=SimpleNamespace(locate=Mock(return_value=registration),boards={'A':{'spaces':[{'space_id':'A-01'}]}})
        p.search_order={};p.confirmed={'c':(1,'A',poly)};p.baselines={}
        p.detector=SimpleNamespace(infer_bgr=Mock(return_value={'inference_ms':1,'detections':[
            dict(bbox_xyxy=[35,35,65,65],confidence=score,sticker_color='green',vehicle_type_from_sticker='ev')]}))
        p.occupancy=SimpleNamespace(scores=lambda _:dict(occupied=.7,empty=.2))
        p.number_model=SimpleNamespace(scores=lambda _: {},labels=[])
        p.numbers=SimpleNamespace(query=lambda *a,**kw:reading,status='ready',worker=None,has_pending=lambda _:False,retain_keys=Mock())
        with patch('web_live.pipeline.crop_quality',return_value=''),patch('web_live.pipeline.annotate',return_value=''):
            return p._analyze(np.full((110,110,3),128,np.uint8),'c',1,1,datetime.now(timezone.utc).isoformat(),'A',.85,time.perf_counter())

    def test_weak_candidate_is_visible_as_diagnostic_but_never_sendable(self):
        r=self.pipeline_result(.055,{'number':'11','confidence':.99,'independent_reads':3})
        row=r['spaces'][0]
        self.assertEqual(row['unconfirmed_sticker_candidates'][0]['confidence'],.055)
        self.assertFalse(row['sendable']);self.assertIsNone(row['car_id']);self.assertIsNone(row['vehicle_type'])
        self.assertEqual(r['detections'],[])

    def test_failed_ocr_reason_is_not_disguised_as_waiting_for_agreement(self):
        r=self.pipeline_result(.95,{'number':None,'confidence':0.,'reason':'OCR 근거 부족','independent_reads':0})
        self.assertEqual(r['spaces'][0]['number_reason'],'OCR 근거 부족')
        self.assertIsNone(r['spaces'][0]['car_id'])

    def transport(self):
        t=EventTransport.__new__(EventTransport);t.lock=threading.RLock()
        t.http=threading.local()
        t.stop_event=threading.Event();t.generation=0
        r,p=fixture();return t,to_events(r,p,'camera_a')[0]

    def test_timed_out_request_is_unconfirmed_when_it_expires_before_retry(self):
        t,e=self.transport();now=datetime.now(timezone.utc);e['observed_at']=now.isoformat()
        clock=Mock();clock.fromisoformat=datetime.fromisoformat
        clock.now.side_effect=[now,now+timedelta(seconds=2.1)]
        opener=Mock();opener.open.side_effect=TimeoutError()
        with patch('web_live.transport.datetime',clock),patch('web_live.transport.urllib.request.build_opener',return_value=opener):
            self.assertEqual(t.send_one(e,lambda _:True,'http://127.0.0.1','test',0),'delivery_unconfirmed')
        self.assertEqual(opener.open.call_count,1)
        self.assertEqual(e['observed_at'],now.isoformat())

    def test_immediate_disconnect_retries_identical_payload_and_id(self):
        t,e=self.transport();e['observed_at']=datetime.now(timezone.utc).isoformat()
        body=json.dumps(dict(event_id=e['event_id'],disposition='duplicate')).encode()
        response=Mock();response.__enter__=Mock(return_value=SimpleNamespace(status=200,length=None,read=lambda limit:body));response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.side_effect=[ConnectionResetError(),response]
        with patch('web_live.transport.urllib.request.build_opener',return_value=opener):
            self.assertEqual(t.send_one(e,lambda _:True,'http://127.0.0.1','test',0),'duplicate')
        self.assertEqual(opener.open.call_args_list[0].args[0].data,opener.open.call_args_list[1].args[0].data)

    def test_never_dispatched_expired_event_is_withheld(self):
        t,e=self.transport();e['observed_at']=(datetime.now(timezone.utc)-timedelta(seconds=3)).isoformat()
        opener=Mock()
        with patch('web_live.transport.urllib.request.build_opener',return_value=opener):
            self.assertEqual(t.send_one(e,lambda _:True,'http://127.0.0.1','test',0),'withheld_or_expired')
        opener.open.assert_not_called()
