import unittest
from unittest.mock import patch
import time

from web_live.tests import test_round29
from web_live.pipeline import probable_paint_glint


class ChangedBayPriorityTests(unittest.TestCase):
    setUp=test_round29.ChangedBayTests.setUp
    frame=test_round29.ChangedBayTests.frame
    offer=test_round29.ChangedBayTests.offer

    def test_fire_on_and_off_publish_same_frame_occupancy_immediately(self):
        self.frame(1,occupied=[0])
        self.offer()
        self.frame(2,occupied=[0])
        result=self.live.results['1']
        light=dict(state='confirmed',location='space',space_id='A-01',method='red_emission',
                   confidence=.95,bbox_xyxy=[20,20,30,30])
        result['fire_lights']=[light]
        result['spaces'][0].update(fire_led=light,sticker_bbox=[10,10,40,40])
        events=self.offer()
        self.assertEqual({e['event_type'] for e in events},{'vehicle_observed','mock_fire_led_observed'})
        self.assertTrue(any(e.get('_vehicle_fire') is True for e in events))
        self.assertEqual(len({e['frame_id'] for e in events}),1)
        self.frame(3,occupied=[0])
        self.assertEqual([e['event_type'] for e in self.offer()],['vehicle_observed'])

    def test_reflection_filter_uses_bay_scale_and_independent_sticker_observation(self):
        light=dict(method='red_emission',bay_area_fraction=.002,confidence=.98)
        white=dict(sticker_color='white',observation='occupied')
        self.assertTrue(probable_paint_glint(light,white))
        for evidence,row in ((dict(light,bay_area_fraction=.02),white),(dict(light,confidence=.995),white),
                             (light,dict(white,sticker_color='green')),(light,None)):
            self.assertFalse(probable_paint_glint(evidence,row))

    def test_changed_bay_precedes_due_heartbeats_in_one_frame(self):
        self.frame(1,occupied=[11])
        self.offer()
        self.frame(2,occupied=[8])
        later=time.monotonic()+1.1
        self.live.packets['1']['received']=later
        with patch('web_live.live.time.monotonic',return_value=later):
            events=self.offer()
        self.assertEqual(len(events),12)
        self.assertEqual([(e['space_id'],e['event_type']) for e in events[:2]],
                         [('B-03','vehicle_observed'),('B-06','space_empty_observed')])
