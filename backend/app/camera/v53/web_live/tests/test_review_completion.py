"""Reviewed-frame binding, bounded photo OCR, and bounded OCR recovery."""
from collections import deque
import copy
from datetime import datetime,timezone
import threading
import time
import unittest
from unittest.mock import Mock,patch
import numpy as np
from web_live.live import LiveSession
from web_live.number_reader import NumberReader
from web_live.pipeline import ParkingPipeline
from web_live.policy import to_events
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub,Transport


class ReviewedFrameTests(unittest.TestCase):
    def setUp(self):
        self.pipeline=ParkingPipeline.__new__(ParkingPipeline)
        self.pipeline.lock=threading.RLock();self.pipeline.numbers=NumberReader()
        self.pipeline.baselines={};self.pipeline.confirmed={};self.pipeline.search_order={}
        self.live=LiveSession(self.pipeline,transport_factory=Transport)
        self.live.hub=Hub([]);self.live.hub.active=True;self.live.running=True
        checked,source=fixture()
        image=np.tile(np.arange(20,dtype=np.uint8)[:,None,None],(1,20,3))*5
        source.update(image=image,received=time.monotonic())
        checked['_outline']=np.float32([[0,0],[19,0],[19,19],[0,19]])
        checked['_crops']={s['space_id']:image.copy() for s in checked['spaces']}
        for row in checked['spaces']:row.update(model_observation='empty',model_score=.7)
        current=copy.deepcopy(checked);current['frame_sequence']=9
        packet=dict(source,sequence=9,observed_at=datetime.now(timezone.utc).isoformat())
        current['observed_at']=packet['observed_at']
        self.checked=checked;self.source=source;self.current=current
        self.live.results={'1':current};self.live.packets={'1':packet};self.live.hub.packets={'1':packet}
        self.live.review_frames={'1':deque([(checked,source),(current,packet)],maxlen=4)}
        self.token={'epoch':source['epoch'],'sequence':source['sequence'],
                    'observed_at':source['observed_at'],'lot_id':'A'}
    def tearDown(self):self.live.close()

    def test_registers_the_reviewed_frame_not_unreviewed_latest_frame(self):
        self.live.confirm('1',empty=True,reviewed=self.token)
        self.assertEqual(self.pipeline.baselines['1']['reviewed_frame']['sequence'],8)
        self.assertEqual(self.pipeline.baselines['1']['reviewed_frame']['observed_at'],self.source['observed_at'])

    def test_review_invalidates_only_earlier_events_from_reviewed_channel(self):
        other,packet=fixture('B')
        packet['received']=time.monotonic()
        self.live.results['2']=other;self.live.packets['2']=packet;self.live.hub.packets['2']=packet
        self.live.sending=True
        old=to_events(self.current,self.live.packets['1'],'camera_a')[0]
        peer=to_events(other,packet,'camera_b')[0]
        self.assertTrue(self.live._valid_event(old))
        self.live.confirm('1',empty=True,reviewed=self.token)
        self.assertFalse(self.live._valid_event(old))
        self.assertTrue(self.live._valid_event(peer))
        self.assertEqual(self.live.transport.cancellations,0)

    def test_missing_frame_and_expired_review_are_rejected(self):
        self.assertRaises(ValueError,self.live.confirm,'1')
        self.assertRaises(ValueError,self.live.confirm,'1',reviewed=dict(self.token,sequence=100))
        self.source['received']-=3
        self.assertRaises(ValueError,self.live.confirm,'1',reviewed=self.token)
        self.assertEqual(self.pipeline.confirmed,{})

    def test_current_pose_change_rejects_old_approval(self):
        self.current['_outline']+=40
        self.assertRaises(ValueError,self.live.confirm,'1',reviewed=self.token)
        self.assertEqual(self.pipeline.confirmed,{})

    def test_new_object_blocks_empty_even_if_current_classifier_misses_it(self):
        self.current['_crops']['A-01']=255-self.current['_crops']['A-01']
        self.assertRaises(ValueError,self.live.confirm,'1',empty=True,reviewed=self.token)
        self.assertEqual(self.pipeline.baselines,{})

    def test_current_occupancy_blocks_reviewed_empty(self):
        self.current['spaces'][0]['model_observation']='occupied'
        self.assertRaises(ValueError,self.live.confirm,'1',empty=True,reviewed=self.token)


class CompletionTests(unittest.TestCase):
    def test_photo_waits_for_final_ocr_result(self):
        pipeline=ParkingPipeline.__new__(ParkingPipeline);pipeline.photo_lock=threading.Lock();pipeline.invalidate=Mock()
        pipeline.analyze=Mock(side_effect=[{'ocr_pending':True},{'ocr_pending':False,'car_id':'23'}])
        with patch('web_live.pipeline.time.sleep'):
            result=pipeline.analyze_photo(object())
        self.assertEqual(result['car_id'],'23');self.assertEqual(pipeline.analyze.call_count,2)
        pipeline.invalidate.assert_called_once_with('photo')

    def test_photo_timeout_remains_explicitly_pending(self):
        pipeline=ParkingPipeline.__new__(ParkingPipeline);pipeline.photo_lock=threading.Lock();pipeline.invalidate=Mock()
        pipeline.analyze=Mock(return_value={'ocr_pending':True})
        result=pipeline.analyze_photo(object(),wait_seconds=0)
        self.assertTrue(result['ocr_pending']);self.assertIn('대기',result['note'])

    def test_recoverable_ocr_failure_clears_old_identity_before_restart(self):
        reader=NumberReader();reader.cache[('1',)]='old identity'
        def recovered():reader.status='OCR 준비 완료'
        with patch.object(reader,'_run_once',side_effect=[TimeoutError(),recovered]) as once, \
             patch.object(reader.stop_event,'wait',return_value=False),self.assertLogs('web_live.number_reader',level='WARNING'):
            # Mock side_effect callables are returned, not called; use a real dispatcher.
            def dispatch():
                if once.call_count==1:raise TimeoutError()
                recovered()
            once.side_effect=dispatch
            reader._run()
        self.assertEqual(once.call_count,2);self.assertEqual(reader.cache,{})
        self.assertEqual(reader.restarts,1);self.assertEqual(reader.status,'OCR 준비 완료')

    def test_persistent_ocr_failure_has_bounded_restarts(self):
        reader=NumberReader()
        with patch.object(reader,'_run_once',side_effect=RuntimeError()) as once, \
             patch.object(reader.stop_event,'wait',return_value=False),self.assertLogs('web_live.number_reader',level='WARNING'):
            reader._run()
        self.assertEqual(once.call_count,3);self.assertEqual(reader.restarts,2)
        self.assertIn('복구 실패',reader.status)


if __name__=='__main__':unittest.main()
