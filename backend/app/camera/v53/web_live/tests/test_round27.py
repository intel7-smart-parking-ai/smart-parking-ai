import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from web_live.empty_board import matches_printed_floor
from web_live.pipeline import corroborated_paper, reread_detector_crop
from web_live.printed_numbers import PrintedNumbers


class PrintedStickerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((160, 160, 3), np.uint8)
        self.hit = dict(number='22', score=.98, margin=.06)
        self.paper = dict(bbox_xyxy=[45, 46, 95, 96], sticker_color='white',
                          localization_source='paper_contour_and_print', print_read=self.hit)
        self.detector = dict(bbox_xyxy=[41, 42, 99, 100], sticker_color='blue')

    def test_nested_paper_requires_current_read_from_second_box(self):
        reader = Mock()
        for hit, expected in ((self.hit, True), (None, False), (dict(self.hit, number='32'), False),
                              (dict(self.hit, margin=.031), False), (dict(self.hit, score=.95), False)):
            with self.subTest(hit=hit):
                reader.read_box.return_value = hit
                self.assertEqual(corroborated_paper(reader, self.frame, self.detector, self.paper), expected)
                self.assertEqual(reader.read_box.call_args.args[1]['bbox_xyxy'], self.detector['bbox_xyxy'])

    def test_other_sheet_or_contradictory_digit_cannot_be_replaced(self):
        reader = Mock()
        for det, paper in ((dict(self.detector, print_read=dict(self.hit, number='32')), self.paper),
                           (self.detector, dict(self.paper, bbox_xyxy=[64,46,114,96])),
                           (self.detector, dict(self.paper, print_read=dict(self.hit, margin=.02))),
                           (self.detector, dict(self.paper, localization_source='rotated_detector'))):
            self.assertFalse(corroborated_paper(reader, self.frame, det, paper))
        reader.read_box.assert_not_called()

    def test_shifted_boxes_are_unique_and_conflicts_hold(self):
        reader = Mock()
        seen = set()
        def read(frame, detection):
            box = tuple(detection['bbox_xyxy'])
            self.assertNotIn(box, seen)
            seen.add(box)
            return dict(self.hit, number='23' if len(seen) == 3 else '22')
        reader.read_box.side_effect = read
        self.assertIsNone(reread_detector_crop(reader, self.frame, self.detector))

    def test_contrast_fallback_requires_agreement_and_original_paper_color(self):
        reader = Mock()
        detection = dict(self.detector, sticker_color='white')
        white = np.full_like(self.frame, 180)
        for extra, expected in (((self.hit,self.hit), self.hit),
                                ((self.hit,dict(self.hit,number='23')), None),
                                ((self.hit,dict(self.hit,margin=.03)), None),
                                ((self.hit,None), None)):
            iterator = iter(extra)
            reader.read_box.side_effect = lambda frame, d: next(iterator) if frame.shape!=white.shape else None
            self.assertEqual(reread_detector_crop(reader, white, detection), expected)
        reader.read_box.side_effect = lambda frame, d: self.fail('used enhanced crop on dark paper') if frame.shape!=white.shape else None
        self.assertIsNone(reread_detector_crop(reader, self.frame, detection))

    def test_high_exposure_floor_still_requires_name_and_unobstructed_surface(self):
        ref = np.full((112,80,3), 55, np.uint8)
        cv2.putText(ref, 'A-06', (17,61), cv2.FONT_HERSHEY_SIMPLEX, .52, (166,166,166), 1, cv2.LINE_AA)
        lit = cv2.resize(ref, (160,224)) + 55
        self.assertTrue(matches_printed_floor(lit, ref))
        for x,y in ((35,40), (62,102), (91,151)):
            covered = lit.copy()
            cv2.rectangle(covered, (x,y), (x+33,y+38), (25,130,184), -1)
            self.assertFalse(matches_printed_floor(covered, ref))

    def test_glowing_light_and_colored_roof_alone_do_not_identify_a_vehicle(self):
        reader = Mock()
        reader.read_beside_light.return_value = None
        frame = self.frame.copy()
        cv2.rectangle(frame,(48,65),(82,94),(55,168,63),-1)
        cv2.circle(frame,(70,49),8,(0,0,255),-1)
        polygon = np.float32([[10,10],[125,10],[125,128],[10,128]])
        self.assertEqual(PrintedNumbers.papers_beside_light(reader,frame,polygon,[62,41,78,57]),[])
        reader.read_beside_light.assert_called()
