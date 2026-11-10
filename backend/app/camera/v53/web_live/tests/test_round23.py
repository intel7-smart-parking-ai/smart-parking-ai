from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from web_live.empty_board import load_empty_board, matches_printed_floor
from web_live.pipeline import repeat_lights


class SurveyedFloorTests(unittest.TestCase):
    def setUp(self):
        self.ref = np.full((112, 80, 3), (46, 43, 41), np.uint8)
        cv2.putText(self.ref, 'B-03', (18, 62), cv2.FONT_HERSHEY_SIMPLEX, .52, (219, 218, 216), 1, cv2.LINE_AA)
        self.frame = cv2.resize(self.ref, (160, 224))

    def test_visible_print_with_global_illumination_change(self):
        self.assertTrue(matches_printed_floor(self.frame + 12, self.ref))

    def test_missing_print_occluder_or_new_colored_object(self):
        for x,y,w,h,color in [(32,85,100,48,(45,45,45)), (45,34,48,49,(71,124,183)),
                              (78,145,48,40,(148,73,36))]:
            with self.subTest(color=color):
                frame=self.frame.copy()
                frame[y:y+h,x:x+w]=color
                self.assertFalse(matches_printed_floor(frame,self.ref))

    def test_missing_and_incomplete_reference_cannot_create_empty_votes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'floor.npz'
            self.assertEqual(load_empty_board(path), {})
            np.savez(path, **{'AA-01': self.ref})
            self.assertEqual(load_empty_board(path), {})

    def test_wide_glint_needs_three_consecutive_frames_in_same_bay(self):
        item=dict(space_id='B-03',location='space',bbox_xyxy=[100,100,186,186],bay_area_fraction=.19)
        history=[]
        for expected in ('candidate','candidate','confirmed'):
            current=[dict(item)]
            repeat_lights(current,history)
            self.assertEqual(current[0]['state'], expected)
            history=current
        current=[dict(item)]
        repeat_lights(current,[])
        self.assertEqual(current[0]['state'],'candidate')
