import unittest
from unittest.mock import patch
import cv2
import numpy as np

from web_live.tests import test_round29
from web_live.registration import BoardRegistry


class FreshnessTests(unittest.TestCase):
    setUp = test_round29.ChangedBayTests.setUp
    frame = test_round29.ChangedBayTests.frame
    offer = test_round29.ChangedBayTests.offer

    def observe(self, seq, captured, ready, fps, held=()):
        with patch('web_live.live.time.monotonic', return_value=ready):
            self.frame(seq, held=held)
            self.live.packets['1']['received'] = captured
            self.live.results['1'].update(_ready_mono=ready, analysis_fps=fps)
            return self.offer()

    def test_next_slow_analysis_would_outlive_previous_frame(self):
        self.assertEqual(len(self.observe(1, 100, 100.55, 1.25)), 12)
        self.assertEqual(self.observe(2, 100.2, 100.7, 4), [])
        sent = self.observe(3, 100.6, 101.12, 1.25, held=(0,))
        self.assertEqual(len(sent), 11)
        self.assertNotIn('A-01', [e['space_id'] for e in sent])
        self.assertEqual(self.observe(3, 100.6, 101.2, 1.25, held=(0,)), [])


class ExposureTests(unittest.TestCase):
    def test_only_matching_board_and_distributed_patterns_can_refresh(self):
        board=BoardRegistry.__new__(BoardRegistry)
        gray=np.random.default_rng(921).integers(10,145,(110,120),dtype=np.uint8)
        frame=cv2.cvtColor(gray+60,cv2.COLOR_GRAY2BGR)
        outline=np.float32([[0,0],[119,0],[119,109],[0,109]])
        pose=dict(lot_id='B',outline=outline)
        landmarks=[(x,y,gray[y-7:y+8,x-7:x+8].copy()) for x in (15,40,70,100) for y in (14,49,90)]
        ref=dict(pose=pose,shape=gray.shape,scale=1.,area=8500.,landmarks=landmarks)
        corrected=board.refresh_fixed_exposure(frame,ref,pose.copy())
        self.assertIsNot(corrected,ref)
        self.assertIs(corrected['pose'],pose)
        for (_,_,old),(_,_,new) in zip(landmarks,corrected['landmarks']):
            np.testing.assert_array_equal(new,old+60)
        for other in [dict(pose,lot_id='A'),dict(pose,outline=outline+4),dict(pose,clipped_spaces=['B-06'])]:
            self.assertIs(board.refresh_fixed_exposure(frame,ref,other),ref)
        self.assertIs(board.refresh_fixed_exposure(np.zeros_like(frame),ref,pose),ref)
