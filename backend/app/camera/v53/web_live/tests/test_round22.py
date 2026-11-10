import unittest
from unittest.mock import Mock

from web_live.pipeline import read_current_sticker
from web_live.printed_numbers import same_print_area
from web_live.policy import to_events, is_vehicle_fire, wire_event
from web_live.tests.test_contract import fixture


class CurrentStickerTests(unittest.TestCase):
    box = [15, 20, 45, 50]

    def test_inner_glyphs_and_distinct_papers(self):
        self.assertTrue(same_print_area(self.box, [20, 25, 40, 45]))
        self.assertFalse(same_print_area(self.box, [35, 24, 58, 47]))
        self.assertFalse(same_print_area(self.box, [50, 20, 80, 50]))

    def read(self, reads, nearby=()):
        reader = Mock()
        reader.read_box.side_effect = lambda frame, d: reads.get(d['sticker_color'])
        return read_current_sticker(reader, object(), self.box, nearby)

    def test_new_print_and_new_color_use_current_pixels(self):
        hit = dict(number='23', score=.97, margin=.06)
        current = self.read({'green': hit})
        self.assertEqual((current['print_read']['number'], current['vehicle_type_from_sticker']), ('23', 'ev'))

    def test_no_inheritance_with_no_read_or_conflicting_evidence(self):
        hit = dict(number='23', score=.97, margin=.06)
        self.assertIsNone(self.read({}))
        self.assertIsNone(self.read({'green': hit, 'white': dict(hit, number='34')}))
        self.assertIsNone(self.read({'green': hit}, [dict(bbox_xyxy=self.box, sticker_color='blue')]))
        self.assertIsNone(self.read({'green': hit}, [dict(bbox_xyxy=[55, 50, 85, 80], sticker_color='green')]))
        self.assertIsNone(self.read({'green': dict(hit, margin=.02)}))


class VehicleFireTests(unittest.TestCase):
    def test_lit_roof_carries_current_vehicle_and_bay(self):
        result, packet = fixture()
        row=result['spaces'][0]
        row.update(observation='occupied', car_id='42', vehicle_type='ev', sticker_bbox=[20,20,40,40])
        light=dict(space_id=row['space_id'],location='space',state='confirmed',bbox_xyxy=[40,21,49,30],confidence=.94)
        result['fire_lights']=[light]
        def fire():
            return [e for e in to_events(result,packet,'camera_a') if 'fire' in e['event_type']]
        event,=fire()
        self.assertEqual((event['event_type'],event['car_id'],event['vehicle_type'],event['space_id']),
                         ('mock_fire_led_observed','42','ev','A-01'))
        self.assertTrue(is_vehicle_fire(event))
        # Rule 6 contract: the HTTP body keeps only the team vision-event fields.
        self.assertEqual(set(wire_event(event)),{'event_id','event_type','source','lot_id','camera_id','space_id',
                                                 'car_id','vehicle_type','confidence','bbox_xyxy','observed_at','frame_id'})
        row['car_id']=None
        self.assertIsNone(fire()[0]['car_id'])
        light['bbox_xyxy']=[140,141,149,150]
        self.assertEqual(fire()[0]['event_type'],'mock_fire_led_observed')
        self.assertFalse(is_vehicle_fire(fire()[0]))
        light['state']='candidate'
        self.assertEqual(fire(),[])
