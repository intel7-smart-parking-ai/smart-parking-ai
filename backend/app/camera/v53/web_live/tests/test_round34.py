import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from web_live.empty_board import load_empty_board
from web_live.pipeline import corroborated_paper


class LocalFloorTests(unittest.TestCase):
    def test_sparse_calibration_only_accepts_surveyed_names_and_shape(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'floor.npz'
            floor=np.zeros((112,80,3),np.uint8)
            np.savez(path,**{'BA-05':floor})
            self.assertEqual(load_empty_board(path),{})
            self.assertEqual(set(load_empty_board(path,partial=True)),{('B','A-05')})
            for data in ({'BA-05':floor,'BA-09':floor},{'BA-05':floor.T}):
                np.savez(path,**data)
                self.assertEqual(load_empty_board(path,partial=True),{})


class PrintedPaperTests(unittest.TestCase):
    def test_overlapping_box_requires_matching_reads_and_separate_papers_stay_separate(self):
        printed=Mock()
        frame=np.full((110,180,3),205,np.uint8)
        reading=dict(number='88',score=.982,margin=.026)
        paper=dict(sticker_color='white',localization_source='paper_contour_and_print',
                   print_read=reading,bbox_xyxy=[35,30,95,84])
        outer=dict(sticker_color='blue',bbox_xyxy=[38,25,101,89])
        printed.read_box.return_value=reading
        self.assertTrue(corroborated_paper(printed,frame,outer,paper))
        printed.read_box.return_value=dict(reading,number='33')
        self.assertFalse(corroborated_paper(printed,frame,outer,paper))
        printed.read_box.return_value=reading
        self.assertFalse(corroborated_paper(printed,frame,dict(outer,bbox_xyxy=[96,25,160,89]),paper))
