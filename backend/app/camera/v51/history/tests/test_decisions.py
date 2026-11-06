import copy
import csv
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from analysis import number,color_candidate,analyze
from contracts import CONTRACT_REVISION,number_config_for_lot
from pipeline import Analyzer,read_json,export_csv,needs_analysis
from parking_ai.storage import atomic_json
from review import save_review,default_review,rows_for_edit,export_reviewed
import test_integration as fixtures
from test_integration import CFG,SPACE,Reader,HIT

class Decisions(unittest.TestCase):
    def record(self,root,lot='A'):
        return fixtures.Contracts().fixture(root,lot)
    def test_no_zero_padding_or_non_two_digit_real_number(self):
        cfg=number_config_for_lot('A',CFG)
        for raw in ('1','001','00','101','1724','17가1724',1):self.assertIsNone(number(raw,cfg),str(raw))
        self.assertEqual(number('01',cfg),'01');self.assertEqual(number('24',cfg),'24')
    def test_virtual_is_separate_from_real(self):
        cfg=number_config_for_lot('C',CFG)
        self.assertEqual(number('17가1724',cfg),'1724');self.assertEqual(number('1724',cfg),'1724')
        self.assertIsNone(number('01',cfg))
    def test_floor_changes_cannot_change_color(self):
        polygon=[[50,20],[80,50],[50,80],[20,50]]
        results=[]
        for floor in ([0,255,0],[255,0,0],[0,0,0]):
            image=np.full((100,100,3),floor,np.uint8)
            cv2.fillConvexPoly(image,np.array(polygon,np.int32),(255,255,255))
            cv2.putText(image,'12',(35,58),0,.7,(0,0,0),2)
            results.append(color_candidate(image,polygon))
        self.assertTrue(all(r['name']=='white' for r in results))
        self.assertEqual(results[0]['ratios'],results[1]['ratios']);self.assertEqual(results[0]['sample_pixels'],results[2]['sample_pixels'])
    def test_no_polygon_no_color_even_on_green_floor(self):
        image=np.full((100,100,3),(0,255,0),np.uint8)
        self.assertEqual(color_candidate(image)['status'],'sticker_region_required')
        row=analyze(image,[SPACE],Reader([HIT]),CFG)[0]
        self.assertEqual(row['number_candidate'],'12');self.assertEqual(row['color_candidate'],'unknown');self.assertIsNone(row['vehicle_type_candidate'])
    def test_rectangle_or_invalid_polygon_not_accepted_as_sticker(self):
        image=np.zeros((100,100,3),np.uint8)
        for region in ([10,10,90,90],[[10,10],[90,90],[10,90],[90,10]],[[-1,10],[90,10],[90,90],[10,90]]):
            with self.assertRaises(ValueError):color_candidate(image,region)
    def test_dark_and_insufficient_sticker_unknown(self):
        result=color_candidate(np.zeros((100,100,3),np.uint8),[[20,20],[80,20],[80,80],[20,80]])
        self.assertEqual(result['name'],'unknown')
    def test_review_known_color_requires_sticker_polygon(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root);rows=default_review(record)
            rows[0].update(occupancy='occupied',plate_text='12',color='green',review_status='approved')
            with self.assertRaisesRegex(ValueError,'스티커'):save_review(root,record,rows,'tester')
    def test_human_mask_evidence_and_id_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root);rows=default_review(record)
            rows[0].update(occupancy='occupied',plate_text='01',color='blue',sticker_polygon=[[20,20],[80,20],[80,80],[20,80]],review_status='approved')
            result=save_review(root,record,rows,'tester')['spaces'][0]
            self.assertEqual(result['car_id'],'01');self.assertEqual(result['vehicle_type'],'accessible')
            self.assertEqual(result['color_measurement']['method'],'sticker_interior_hsv_v1')
    def test_virtual_full_plate_never_becomes_real_car_id(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root,'C');rows=default_review(record)
            rows[0].update(occupancy='occupied',plate_text='17가1724',review_status='approved')
            result=save_review(root,record,rows,'tester')['spaces'][0]
            self.assertEqual(result['display_number'],'1724');self.assertEqual(result['plate_text'],'17가1724');self.assertIsNone(result['car_id'])
    def test_legacy_review_reopened_pending_and_export_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root);rows=default_review(record);rows[0].update(plate_text='1',color='green',review_status='approved')
            legacy={'image_id':record['image_id'],'lot_id':'A','reviewer':'old','spaces':rows}
            atomic_json(root/'reviews'/path.name,legacy)
            edited=rows_for_edit(record,legacy)[0];self.assertEqual(edited['review_status'],'pending');self.assertEqual(edited['color'],'unknown')
            target,count=export_reviewed(root);self.assertEqual(count,0)
            self.assertEqual(len(read_json(target.with_suffix('.report.json'))['skipped']),1)
            self.assertEqual(read_json(root/'reviews'/path.name)['spaces'][0]['color'],'green')
    def test_old_ocr_predictions_not_exported_and_reprocessed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root);record['analysis_status']='complete';record.pop('analysis_revision',None);atomic_json(path,record)
            atomic_json(root/'analysis'/path.name,{'spaces':[{'space_id':'A-01','number_candidate':'1','color_candidate':'green','vehicle_type_candidate':'electric'}]})
            self.assertTrue(needs_analysis(record))
            with export_csv(root).open(encoding='utf-8-sig',newline='') as f:row=next(csv.DictReader(f))
            self.assertEqual(row['number_candidate'],'');self.assertEqual(row['color_candidate'],'unknown')
            worker=Analyzer(root,root,CFG);worker.reader=Reader([HIT]);worker.engine={'device':'test'};worker.process(path)
            self.assertFalse(needs_analysis(read_json(path)));self.assertEqual(read_json(root/'analysis'/path.name)['contract_revision'],CONTRACT_REVISION)
    def test_old_capture_config_cannot_enable_single_digit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root);record['analysis_config']['number_mode']='virtual_plate';atomic_json(path,record)
            worker=Analyzer(root,root,CFG);worker.reader=Reader([(HIT[0],'1',.99)]);worker.engine={};worker.process(path)
            self.assertIsNone(read_json(root/'analysis'/path.name)['spaces'][0]['number_candidate'])

if __name__=='__main__':unittest.main()
