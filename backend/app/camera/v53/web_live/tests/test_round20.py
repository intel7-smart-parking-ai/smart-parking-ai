"""Regression coverage for low-score SSD work and physical ROI approval latency."""
import time
import unittest
from unittest.mock import Mock
import numpy as np
from web_live.inference import thresholded_nms
from web_live.live import LiveSession
from web_live.tests.test_lifecycle import Hub,Pipeline,Transport
from model_api.models.utils import calculate_nms


class StickerSuppressionTests(unittest.TestCase):
    def test_indices_and_classes_match_adapter(self):
        boxes=np.float32([[0,0,10,10],[20,20,30,30],[0,0,9,10],[0,0,10,10]])
        scores=np.float32([.92,.02,.90,.85])
        labels=np.int32([0,1,0,1])
        def original(boxes,scores,labels,include_boundaries=False):
            return calculate_nms(boxes,scores,labels,include_boundaries=include_boundaries,execute_nms=True)
        expected=[i for i in original(boxes,scores,labels) if scores[i]>.05]
        self.assertEqual(thresholded_nms(original,.05)(boxes,scores,labels),expected)

    def test_ties_preserve_original_adapter_order_and_empty_skips_work(self):
        original=Mock(return_value=[2,0,1])
        call=thresholded_nms(original,.05)
        boxes=np.zeros((3,4));labels=np.zeros(3)
        self.assertEqual(call(boxes,np.array([.04,.05,.02]),labels),[])
        original.assert_not_called()
        self.assertEqual(call(boxes,np.array([.8,.04,.8]),labels),[2,0,1])
        self.assertIs(original.call_args.args[0],boxes)


class SlowBoardReviewTests(unittest.TestCase):
    def setUp(self):
        p=Pipeline();p.registry=Mock();p.approve_roi=Mock()
        self.live=LiveSession(p,transport_factory=Transport)
        self.live.hub=Hub([]);self.live.hub.feed('1',seq=12)
        now=self.live.hub.latest('1')
        self.old=dict(now,sequence=10,observed_at='old',received=time.monotonic()-3)
        shape=np.float32([[0,0],[0,10],[10,10],[10,0]])
        self.r=dict(stream_epoch=1,frame_sequence=10,lot_id='B',registration_ok=True,
                    observed_at='old',_outline=shape,_pose={'outline':shape})
        self.live.results['1']=self.r;self.live.packets['1']=self.old
        self.live.review_frames['1']=[(self.r,self.old)]
        self.token=dict(epoch=1,sequence=10,lot_id='B',observed_at='old')

    def test_slow_review_requires_fresh_geometry_and_sets_packet_cutoff(self):
        self.live.confirm('1',reviewed=self.token)
        self.live.pipeline.registry.verify_fixed_view.assert_called_once()
        self.assertEqual(self.live.review_cutoff['1'],(1,12))

    def test_shifted_board_or_old_empty_frame_is_rejected(self):
        self.live.pipeline.registry.verify_fixed_view.return_value=None
        with self.assertRaises(ValueError):self.live.confirm('1',reviewed=self.token)
        with self.assertRaises(ValueError):self.live.confirm('1',empty=True,reviewed=self.token)
        self.live.pipeline.approve_roi.assert_not_called()

class RedEmitterTests(unittest.TestCase):
    def test_both_emitters_reappear_at_their_own_positions(self):
        from web_live.pipeline import repeat_lights
        first=[dict(space_id=k,location='space',bbox_xyxy=b) for k,b in
               [('A-07',[1,1,30,30]),('A-08',[90,5,100,15])]]
        repeat_lights(first,[])
        self.assertEqual([v['state'] for v in first],['candidate','candidate'])
        next_frame=[dict(v) for v in first]
        repeat_lights(next_frame,first)
        self.assertEqual([v['state'] for v in next_frame],['confirmed','confirmed'])
        next_frame[0]['bbox_xyxy']=[80,80,100,100]
        repeat_lights(next_frame,first)
        self.assertEqual(next_frame[0]['state'],'candidate')

    def test_candidate_glint_does_not_send_a_fire_event(self):
        from web_live.tests.test_contract import fixture
        from web_live.policy import to_events
        result,packet=fixture('B')
        result['fire_lights']=[dict(space_id='A-07',location='space',confidence=.93,
                                     bbox_xyxy=[1,2,14,15],state='candidate')]
        events=to_events(result,packet,'camera_b')
        self.assertFalse(any(e['event_type']=='mock_fire_led_observed' for e in events))
        result['fire_lights'][0]['state']='confirmed'
        self.assertTrue(any(e['event_type']=='mock_fire_led_observed' for e in to_events(result,packet,'camera_b')))
