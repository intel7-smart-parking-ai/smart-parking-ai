import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from automatic_setup import prepare_auto_scene
from bay_name_retry import centers_to_image, localized_read, validate_local_reads
from dual_capture import RegistrationHold
from layouts import validate_snapshot
from test_v23_open_u import open_board
from automatic_setup import draw_setup_candidates
from open_boundaries import painted_closed_quads


def hit(pixels, name, score=.95):
    h, w = pixels.shape[:2]
    points = np.array([[.30,.30],[.70,.30],[.70,.70],[.30,.70]]) * (w,h)
    return points, name, score


class LocalPrintedName(unittest.TestCase):
    def setUp(self):
        self.image = np.full((90,140,3), 50, np.uint8)
        self.image[32:55,42:92] = 180
        self.center = np.array([70,45])
        self.size = np.array([44,20])

    def read(self, reader, turns=0):
        return localized_read(self.image, reader, 'A-01', self.center, self.size, turns, .7)

    def test_agreement_in_four_camera_rotations_maps_to_original_pixels(self):
        for turns in range(4):
            reader = Mock()
            reader.readtext.side_effect = lambda pixels, **kw: [hit(pixels,'A-01')]
            row = self.read(reader, turns)
            self.assertEqual(reader.readtext.call_count, 2)
            validate_local_reads(row,[140,90],.7)
            midpoint = (np.array(row['bbox'][:2]) + row['bbox'][2:])/2
            np.testing.assert_allclose(midpoint, self.center, atol=2)

    def test_partial_or_single_or_uncertain_read_never_fills_bay(self):
        for seq in ([('A-0',.99)]*3, [('A-01',.98),('A-01',.6),('A-01',.6)],
                    [('1',.99)]*3, [('A-01',.99),('B-01',.98)]):
            reader = Mock()
            answers = iter(seq)
            reader.readtext.side_effect = lambda pixels, **kw: [hit(pixels,*next(answers,('',0.)))]
            self.assertIsNone(self.read(reader))

    def test_flat_crop_skips_ocr_and_two_rows_are_required(self):
        self.image[:] = 22
        reader = Mock()
        self.assertIsNone(self.read(reader))
        reader.readtext.assert_not_called()
        self.assertIsNone(centers_to_image({f'A-{n:02d}':{'bbox':[n,1,n+1,2]} for n in range(1,7)}))

    def test_two_nearby_text_boxes_or_out_of_window_text_rejected(self):
        reader = Mock()
        reader.readtext.side_effect = lambda pixels, **kw:[hit(pixels,'A-01')]*2
        self.assertIsNone(self.read(reader))
        def away(pixels, **kw):
            points, name, score = hit(pixels,'A-01')
            return [(points + [pixels.shape[1],0],name,score)]
        reader.readtext.side_effect = away
        self.assertIsNone(self.read(reader))

    def test_complete_normal_registration_and_reloaded_evidence_integrity(self):
        _, packet, full = open_board()
        full.observations = [r for r in full.observations if r[1] != 'A-01']
        reader = Mock()
        reader.readtext.side_effect = lambda im, **kw: (
            full.readtext(im, **kw) if im.ndim==3 else [hit(im,'A-01')])
        with tempfile.TemporaryDirectory() as temp:
            result = prepare_auto_scene(Path(temp),packet,'A','pose',lambda:reader)
            self.assertTrue(result['ready'])
            original = result['roi']
            for field, value in [('text','B-01'),('mode','gray'),('bbox',[1,1,3,2]),('score',.1)]:
                roi = copy.deepcopy(original)
                row = next(r for r in roi['auto_validation']['text_evidence'] if r.get('localized_retry'))
                row['local_reads'][1][field] = value
                with self.assertRaises(ValueError):
                    validate_snapshot(roi)

    def test_registration_hold_distinguishes_no_print_from_missing_bay(self):
        packet = Mock(camera_id='B')
        setup = {'detected_count':0,'expected_count':16,'text_evidence':[]}
        self.assertIn('판독 0개', str(RegistrationHold(packet,'B',setup)))
        setup.update(detected_count=15,text_evidence=[{'space_id':'A-01'}],missing=['B-08'])
        self.assertIn('미등록 B-08', str(RegistrationHold(packet,'B',setup)))

    def test_three_painted_contour_preserves_unpainted_entrance(self):
        import cv2
        q = [[40,40],[190,40],[190,160],[40,160]]
        im = np.full((210,260,3),40,np.uint8)
        cv2.polylines(im,[np.int32(q)],False,(240,240,240),3)
        candidate, = painted_closed_quads(im,[q])
        self.assertFalse(candidate['_painted_closed'])
        open_u = candidate['_three_painted']
        self.assertEqual(set(map(tuple,open_u['polygon'][2:])),{(40,40),(40,160)})
        self.assertTrue(all(score >= .72 for score in open_u['line_support']))
        self.assertNotIn('_three_painted',painted_closed_quads(im*0,[q])[0])

    def test_two_adjacent_bays_have_separate_labels_inside_their_interiors(self):
        im = np.full((240,300,3),60,np.uint8)
        slots = [{'space_id':'A-01','polygon':[[20,120],[20,20],[250,20],[250,120]]},
                 {'space_id':'A-02','polygon':[[20,120],[250,120],[250,220],[20,220]]}]
        overlay = draw_setup_candidates(im,slots)
        for band in (overlay[24:116,24:246],overlay[124:216,24:246]):
            self.assertGreater(np.all(band == [27,34,31],axis=2).sum(),30)
        self.assertTrue(np.all(im == 60))
