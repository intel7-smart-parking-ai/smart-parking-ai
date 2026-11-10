import tempfile
import unittest
import zipfile
from pathlib import Path

import cv2
import numpy as np

from web_live.policy import CATALOG
from web_live.printed_numbers import PrintedNumbers, enroll, same_box
from web_live.registration import BoardRegistry


class PrintedNumberTests(unittest.TestCase):
    def test_flat_or_tiny_patch_never_recognized(self):
        reader=PrintedNumbers(sorted(CATALOG),np.ones((30,2,32,24),np.float32))
        for size in (10, 60):
            self.assertIsNone(reader.read(np.full((size,size,3),180,np.uint8),'general'))

    def test_incomplete_archive_preserves_previous_enrollment(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'bank.npz'
            output.write_bytes(b'previous')
            archive=Path(folder)/'input.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('11.png',cv2.imencode('.png',np.zeros((25,25,3),np.uint8))[1].tobytes())
            with self.assertRaises(ValueError):enroll(archive,output)
            self.assertEqual(output.read_bytes(),b'previous')

    def test_disjoint_prints_do_not_merge(self):
        self.assertTrue(same_box([5,5,35,35],[6,6,34,34]))
        self.assertFalse(same_box([5,5,35,35],[31,5,61,35]))

    def test_fixed_landmarks_require_current_image_evidence(self):
        registry=BoardRegistry.__new__(BoardRegistry)
        rng=np.random.default_rng(34)
        frame=rng.integers(0,256,(320,560,3),dtype=np.uint8)
        poly1=np.float32([[50,60],[130,60],[130,250],[50,250]])
        poly2=poly1+[300,0]
        pose={'polygons':{'A-01':poly1,'B-01':poly2},'lot_id':'A'}
        ref=registry.fixed_view(frame,pose)
        self.assertIsNotNone(registry.verify_fixed_view(frame,ref))
        self.assertIsNone(registry.verify_fixed_view(np.roll(frame,6,axis=1),ref))
        self.assertIsNone(registry.verify_fixed_view(np.zeros_like(frame),ref))
