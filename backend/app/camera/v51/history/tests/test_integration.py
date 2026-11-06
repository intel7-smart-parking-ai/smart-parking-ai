import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from layouts import layout,initialize,snapshot,load_calibration,save_calibration,validate_spaces,zone_rule
from analysis import analyze,number,color_candidate
from camera_hub import FramePacket
from pipeline import save_capture,read_json,Analyzer,Writer,export_csv
from review import default_review,save_review,export_reviewed

CFG={'max_digits':4,'allowed_numbers':None,'ocr_min_score':.7,'color_margin':.35,'color_to_type':{'white':'general','green':'electric','blue':'accessible'}}
SPACE={'space_id':'A-01','space_type':'general','polygon':[[10,10],[90,10],[90,90],[10,90]]}
class Reader:
    def __init__(self,hits=None):self.hits=hits or []
    def readtext(self,*a,**kw):return self.hits
HIT=([[20,20],[40,20],[40,40],[20,40]],'12',.99)

class Contracts(unittest.TestCase):
    def fixture(self,root,lot='A',camera='A'):
        roi=snapshot(lot,camera,'serial',[100,100],'fixed',[SPACE])
        packet=FramePacket(camera,'serial',1,'2026-11-03T00:00:00Z',1.,np.zeros((100,100,3),np.uint8))
        path=save_capture(root,packet,roi,'batch','session',CFG)
        return path,read_json(path)
    def test_exact_counts(self):
        self.assertEqual([layout(x)['space_count'] for x in 'ABCD'],[12,16,12,10])
    def test_exact_special_spaces(self):
        expected={'A':({'A-05','A-06'},{'B-06'}),'B':({'A-07','A-08'},{'B-07','B-08'}),'C':({'A-05','A-06'},{'B-06'}),'D':({'A-04','A-05'},{'B-05'})}
        for lot,(ev,access) in expected.items():
            spaces=layout(lot)['spaces'];self.assertEqual({s['space_id'] for s in spaces if s['space_type']=='electric'},ev)
            self.assertEqual({s['space_id'] for s in spaces if s['space_type']=='accessible'},access)
    def test_no_fabricated_coordinates(self):
        for lot in 'ABCD':self.assertTrue(all(s['polygon'] is None for s in layout(lot)['spaces']))
    def test_calibration_camera_is_not_lot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);initialize(root);roi=snapshot('B','A','serial',[100,100],'v1',[SPACE]);save_calibration(root,roi)
            self.assertEqual(load_calibration(root,'B','A','serial',[100,100],'v1')['lot_id'],'B')
            self.assertIsNone(load_calibration(root,'B','B','serial',[100,100],'v1'))
            self.assertIsNone(load_calibration(root,'B','A','serial',[200,100],'v1'))
            self.assertIsNone(load_calibration(root,'B','A','serial',[100,100],'v2'))
    def test_unknown_duplicate_crossed_and_overlap_rejected(self):
        invalid=[[{**SPACE,'space_id':'C-01'}],[SPACE,SPACE],
                 [{**SPACE,'polygon':[[10,10],[90,90],[90,10],[10,90]]}],
                 [SPACE,{**SPACE,'space_id':'A-02'}]]
        for spaces in invalid:
            with self.assertRaises(ValueError):validate_spaces('A',spaces,[100,100])
    def test_wrong_special_type_rejected(self):
        with self.assertRaises(ValueError):validate_spaces('A',[{**SPACE,'space_id':'A-05'}],[100,100])
    def test_zone_rules(self):
        for t in ('electric','accessible'):self.assertEqual(zone_rule(t,'general'),'not_allowed')
        self.assertEqual(zone_rule('accessible','electric'),'not_allowed')
        self.assertEqual(zone_rule('electric','electric'),'allowed');self.assertEqual(zone_rule('general','accessible'),'allowed')
        self.assertEqual(zone_rule('electric',None),'unknown')
    def test_plate_display_and_leading_zero(self):
        self.assertEqual(number('17가1724',{**CFG,'number_mode':'virtual_plate'}),'1724');self.assertEqual(number('01',CFG),'01')
        self.assertIsNone(number('171724',CFG));self.assertIsNone(number('abc',CFG))
    def test_color_mapping(self):
        for bgr,name,kind in (([255,255,255],'white','general'),([0,255,0],'green','electric'),([255,0,0],'blue','accessible')):
            image=np.full((100,100,3),bgr,np.uint8)
            self.assertEqual(color_candidate(image,[[30,30],[50,30],[50,50],[30,50]])['name'],name)
            row=analyze(image,[SPACE],Reader([HIT]),CFG)[0]
            self.assertIsNone(row['vehicle_type_candidate'])  # No sticker mask from OCR.
    def test_no_text_is_unknown(self):
        row=analyze(np.zeros((100,100,3),np.uint8),[SPACE],Reader(),CFG)[0]
        self.assertEqual(row['occupancy'],'unknown');self.assertEqual(row['status'],'no_text')
    def test_ocr_coordinate_transform(self):
        row=analyze(np.zeros((100,100,3),np.uint8),[SPACE],Reader([HIT]),CFG)[0]
        self.assertEqual(row['candidates'][0]['text_bbox'],[30,30,50,50])
    def test_rotation_low_score_multi_text_are_withheld(self):
        for hits in ([(*HIT[:1],'06',.99)],[(*HIT[:1],'12',.1)],[HIT,HIT]):
            row=analyze(np.zeros((100,100,3),np.uint8),[SPACE],Reader(hits),CFG)[0];self.assertIsNone(row['number_candidate'])
    def test_duplicate_number(self):
        rows=analyze(np.zeros((100,100,3),np.uint8),[SPACE,{**SPACE,'space_id':'A-02'}],Reader([HIT]),CFG)
        self.assertTrue(all(r['status']=='duplicate_number' for r in rows))
    def test_roi_snapshot_immutable_and_pending(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.fixture(root)
            self.assertEqual(record['lot_id'],'A');self.assertEqual(record['analysis_status'],'pending');self.assertEqual(record['roi']['spaces'][0]['space_id'],'A-01')
            self.assertEqual(record['source']['kind'],'camera')
    def test_same_slot_in_two_lots_is_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,a=self.fixture(root,'A');_,b=self.fixture(root,'B')
            self.assertNotEqual((a['lot_id'],'A-01'),(b['lot_id'],'A-01'))
            text=export_csv(root).read_text(encoding='utf-8-sig');self.assertIn('lot_id',text);self.assertEqual(len(text.splitlines()),3)
    def test_analysis_does_not_approve(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.fixture(root)
            worker=Analyzer(root,root,CFG);worker.reader=Reader([HIT]);worker.engine={'device':'test'};worker.process(path)
            result=read_json(root/'analysis'/path.name)
            self.assertEqual(result['review_status'],'pending');self.assertEqual(result['lot_id'],'A');self.assertFalse((root/'reviews').exists())
    def test_review_export_only_approved_and_special_rule(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.fixture(root)
            record['roi']['spaces'][0].update(space_id='A-05',space_type='electric')
            from parking_ai.storage import atomic_json
            atomic_json(path,record)
            rows=default_review(record);rows[0].update(occupancy='occupied',plate_text='12',color='white',plate_bbox=[20,20,60,60],sticker_polygon=[[20,20],[60,20],[60,60],[20,60]])
            save_review(root,record,rows,'tester');_,count=export_reviewed(root);self.assertEqual(count,0)
            rows[0]['review_status']='approved';saved=save_review(root,record,rows,'tester')
            self.assertEqual(saved['spaces'][0]['zone_rule'],'not_allowed')
            target,count=export_reviewed(root);self.assertEqual(count,1);self.assertIn('12',target.read_text(encoding='utf-8'))
    def test_vacant_cannot_have_number_and_approval_requires_reviewer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.fixture(root);rows=default_review(record)
            with self.assertRaises(ValueError):save_review(root,record,rows,'')
            rows[0].update(occupancy='vacant',plate_text='12')
            with self.assertRaises(ValueError):save_review(root,record,rows,'tester')
    def test_worker_save_failure_does_not_kill_thread(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);worker=Writer(root);worker.start()
            packet=FramePacket('A','serial',1,'now',1.,np.zeros((100,100,3),np.uint8));roi=snapshot('A','A','serial',[100,100],'v',[SPACE])
            try:
                with patch('pipeline.save_capture',side_effect=OSError('disk full')), patch('pipeline.logging.exception') as log:
                    worker.jobs.put((packet,roi,'b','s',CFG));c,path,error=worker.results.get(timeout=3);self.assertIn('disk full',error)
                    self.assertTrue(log.called)
                worker.jobs.put((packet,roi,'b','s',CFG));self.assertIsNone(worker.results.get(timeout=3)[2])
            finally:
                worker.jobs.put(None);worker.join(3)
            self.assertFalse(worker.is_alive())


if __name__=='__main__':unittest.main()
