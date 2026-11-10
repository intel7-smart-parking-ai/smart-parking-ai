import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from web_live.body_decoration import decoration_match
from web_live.number_reader import NumberReader
from web_live.pipeline import ParkingPipeline
from web_live.registration import BoardRegistry


class ReconnectedBoardTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.random.default_rng(15).integers(0, 256, (320, 560, 3), dtype=np.uint8)
        poly = np.float32([[50,60],[130,60],[130,250],[50,250]])
        self.pose = dict(lot_id="A", polygons={"A-01":poly,"B-01":poly+[300,0]}, outline=poly,
                         matrix=np.eye(3), inliers=30, residual_px=0., line_support=.9)
        p = self.p = ParkingPipeline.__new__(ParkingPipeline)
        p.lock = threading.RLock()
        p.numbers = NumberReader()
        p.registry = BoardRegistry.__new__(BoardRegistry)
        p.registry.boards = {"A":{"spaces":[]}}
        p.registry.locate = Mock(return_value=None)
        ref = p.registry.fixed_view(self.frame, self.pose)
        p.fixed_views = {"1":(7,ref),"2":(4,ref)}
        p.confirmed = {"1":(7,"A",poly),"2":(4,"B",poly)}
        p.baselines = {"1":object(),"2":object()}
        p.search_order = {}
        p.detector = Mock()
        p.detector.infer_bgr.return_value = {"detections":[],"inference_ms":0.}
        p.invalidate("1", disconnected=True)

    def analyze(self, image, hint="auto"):
        with patch("web_live.pipeline.annotate", return_value=""):
            return self.p.analyze(image, camera="1", epoch=8, lot_hint=hint)

    def test_current_frame_can_be_reviewed_without_restoring_old_approval(self):
        self.assertFalse(self.analyze(np.zeros_like(self.frame))["registration_ok"])
        self.assertFalse(self.analyze(np.roll(self.frame, 10, axis=1))["registration_ok"])
        self.assertFalse(self.analyze(self.frame, "B")["registration_ok"])
        r = self.analyze(self.frame)
        self.assertTrue(r["registration_ok"])
        self.assertEqual(r["registration"]["method"], "reviewed_fixed_landmarks")
        self.assertFalse(r["roi_confirmed"])
        self.assertFalse(r["empty_reference"])
        self.assertEqual(set(self.p.confirmed), {"2"})
        self.assertEqual(set(self.p.baselines), {"2"})
        self.p.approve_roi("1", r, self.frame)
        self.assertTrue(self.analyze(self.frame)["roi_confirmed"])
        self.assertFalse(self.analyze(self.frame)["empty_reference"])

    def test_new_configuration_and_other_invalidation_forget_geometry(self):
        self.p.invalidate("1")
        self.assertFalse(self.analyze(self.frame)["registration_ok"])
        self.assertIn("2", self.p.fixed_views)
        self.p.invalidate()
        self.assertFalse(self.p.fixed_views)


class RoofPrintTests(unittest.TestCase):
    def test_close_print_requires_separate_consistent_outline_and_matching_trim(self):
        ref = cv2.GaussianBlur(np.random.default_rng(83).integers(0,256,(96,96),dtype=np.uint8),(3,3),.6)
        frame = np.zeros((110,200,3), np.uint8)
        frame[5:69,5:85] = cv2.cvtColor(ref[12:76,10:90], cv2.COLOR_GRAY2BGR)
        trim = dict(bbox_xyxy=[5,5,85,69], sticker_color="white", print_read=None)
        roof = dict(bbox_xyxy=[105,5,185,69], sticker_color="white",
                    print_read=dict(number="58",score=.968,margin=.033))
        paper = dict(roof, print_read=dict(number="58",score=.967,margin=.039))
        def match(papers):
            return decoration_match(frame, [trim,roof], ref, recheck_paper=lambda:papers)
        self.assertIs(match([paper])[0], trim)
        for papers in ([], [dict(paper,sticker_color="green")], [dict(paper,print_read=roof["print_read"])],
                       [dict(paper,print_read=dict(paper["print_read"],number="85"))],
                       [paper,dict(paper,bbox_xyxy=trim["bbox_xyxy"])]):
            with self.subTest(papers=papers):
                self.assertIsNone(match(papers))
        frame[5:40,5:45] = 150
        self.assertIsNone(match([paper]))
