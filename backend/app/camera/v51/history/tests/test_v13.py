import unittest,tempfile,json
from pathlib import Path
import numpy as np
from analysis import analyze
from multi_vehicle import summarize,group_reviewed
from test_integration import CFG,Reader,HIT
class MultiCars(unittest.TestCase):
    def spaces(self):return [{'space_id':f'A-{i+1:02d}','space_type':'general','polygon':[[i*100+5,5],[i*100+95,5],[i*100+95,95],[i*100+5,95]]} for i in range(3)]
    def test_all_three_slots_in_one_frame(self):
        class Sequential:
            def __init__(self):self.n=0
            def readtext(self,*args,**kwargs):
                text=['12','49','100'][self.n];self.n+=1;return [(HIT[0],text,.99)]
        rows=analyze(np.zeros((100,300,3),np.uint8),self.spaces(),Sequential(),CFG)
        self.assertEqual([r['number_candidate'] for r in rows],['12','49','100'])
        summary=summarize(rows);self.assertEqual(summary['number_candidate_spaces'],3);self.assertIsNone(summary['vehicle_count'])
        self.assertGreater(summary['instances'][2]['text_bbox'][0],200)
    def test_multiple_in_same_slot_all_retained_but_not_assigned(self):
        rows=analyze(np.zeros((100,300,3),np.uint8),self.spaces()[:1],Reader([HIT,(HIT[0],'49',.99)]),CFG)
        self.assertIsNone(rows[0]['number_candidate']);self.assertEqual(summarize(rows)['text_candidate_count'],2)
    def test_cross_slot_duplicate_withheld(self):
        rows=analyze(np.zeros((100,300,3),np.uint8),self.spaces(),Reader([HIT]),CFG)
        self.assertTrue(all(r['number_candidate'] is None for r in rows));self.assertEqual(summarize(rows)['text_candidate_count'],3)
    def test_unread_is_not_vacant(self):
        rows=analyze(np.zeros((100,300,3),np.uint8),self.spaces(),Reader(),CFG)
        self.assertEqual(summarize(rows)['needs_review_spaces'],3);self.assertTrue(all(r['occupancy']=='unknown' for r in rows))
    def test_frame_dataset_groups_all_approved_slots(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'reviewed.jsonl';target=root/'frames.jsonl'
            base={'image_id':'frame','raw_path':'raw/a.png','image_size':[300,100],'lot_id':'A','batch_id':'batch','source':{'kind':'camera'}}
            source.write_text('\n'.join(json.dumps({**base,'space_id':s['space_id']}) for s in self.spaces()),encoding='utf-8')
            self.assertEqual(group_reviewed(source,target),1)
            frame=json.loads(target.read_text(encoding='utf-8'));self.assertEqual(len(frame['annotations']),3);self.assertFalse(frame['all_objects_in_image_annotated'])
