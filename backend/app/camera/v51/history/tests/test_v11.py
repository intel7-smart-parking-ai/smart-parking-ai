import unittest,tempfile
from pathlib import Path
from analysis import number,analyze
from review import save_review,default_review,export_reviewed
from test_integration import CFG,SPACE,Reader,HIT
import test_integration as fixture
import numpy as np
class Numbers100(unittest.TestCase):
    def test_range(self):
        for n in range(1,101):
            text=f'{n:02d}';self.assertEqual(number(text,CFG),text)
        for text in ['00','0','1','001','0100','101','999','1.0','１００']:
            self.assertIsNone(number(text,CFG))
    def test_ocr_49_and_100(self):
        for text in ['49','100']:
            rows=analyze(np.zeros((100,100,3),np.uint8),[SPACE],Reader([(HIT[0],text,.99)]),CFG)
            self.assertEqual(rows[0]['number_candidate'],text)
    def test_approved_export_100(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=fixture.Contracts().fixture(root);rows=default_review(record)
            rows[0].update(occupancy='occupied',plate_text='100',review_status='approved')
            saved=save_review(root,record,rows,'tester');self.assertEqual(saved['spaces'][0]['car_id'],'100')
            path,count=export_reviewed(root);self.assertEqual(count,1);self.assertIn('100',path.read_text(encoding='utf-8'))
