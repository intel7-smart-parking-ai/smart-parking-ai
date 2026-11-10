from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from web_live.body_decoration import decoration_match, load_reference


class BodyDecorationTests(unittest.TestCase):
    def setUp(self):
        noise=np.random.default_rng(19).integers(0,255,(96,96),dtype=np.uint8)
        self.ref=cv2.GaussianBlur(noise,(3,3),.6)
        self.frame=np.zeros((100,200,3),np.uint8)
        self.frame[5:69,5:85]=cv2.cvtColor(self.ref[12:76,10:90],cv2.COLOR_GRAY2BGR)
        self.ornament={'bbox_xyxy':[5,5,85,69],'sticker_color':'white','print_read':None}
        self.roof={'bbox_xyxy':[105,5,185,69],'sticker_color':'blue',
                   'print_read':{'number':'77','score':.96,'margin':.05}}
        self.candidates=[self.roof,self.ornament]

    def test_positive_match_is_order_and_identity_independent(self):
        for boxes in (self.candidates,self.candidates[::-1]):
            extra,score=decoration_match(self.frame,boxes,self.ref)
            self.assertIs(extra,self.ornament)
            self.assertGreater(score,.99)

    def test_unreadable_or_partly_overwritten_pattern_is_held(self):
        self.frame[5:37,5:45]=150
        self.assertIsNone(decoration_match(self.frame,self.candidates,self.ref))
        self.frame[:]=150
        self.assertIsNone(decoration_match(self.frame,self.candidates,self.ref))

    def test_two_reads_including_same_number_and_three_candidates_are_held(self):
        self.assertIsNone(decoration_match(self.frame,self.candidates+[self.roof],self.ref))
        self.ornament['print_read']=self.roof['print_read']
        self.assertIsNone(decoration_match(self.frame,self.candidates,self.ref))

    def test_missing_reference_weak_roof_or_overlapping_boxes_are_held(self):
        self.assertIsNone(decoration_match(self.frame,self.candidates,None))
        self.roof['print_read']['margin']=.035
        self.assertIsNone(decoration_match(self.frame,self.candidates,self.ref))
        self.roof['print_read']['margin']=.05
        self.roof['bbox_xyxy']=[78,5,160,69]
        self.assertIsNone(decoration_match(self.frame,self.candidates,self.ref))

    def test_changed_reference_does_not_use_old_crop_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            self.assertIsNone(load_reference(directory))
            (directory/'samples').mkdir()
            (directory/'samples/B_rectified_sample.jpg').write_bytes(b'another reference photo')
            self.assertIsNone(load_reference(directory))
