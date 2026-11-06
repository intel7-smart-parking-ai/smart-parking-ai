"""Regression cases discovered in the V8 full review."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import queue
from unittest.mock import patch
import numpy as np
from camera_hub import FramePacket,load_camera_specs
from layouts import snapshot
from pipeline import save_capture,read_json,export_csv,Analyzer,needs_analysis
from review import default_review,save_review,review_revision,export_reviewed
import test_integration as fixture

class V9Regressions(unittest.TestCase):
    def record(self,root):return fixture.Contracts().fixture(root)
    def test_reject_frame_roi_serial_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            roi=snapshot('A','A','old-camera',[100,100],'pose',[fixture.SPACE])
            packet=FramePacket('A','new-camera',1,'now',1.,np.zeros((100,100,3),np.uint8))
            with self.assertRaises(ValueError):save_capture(Path(d),packet,roi,'b','s',fixture.CFG)
    def test_reject_frame_roi_resolution_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            roi=snapshot('A','A','camera',[200,100],'pose',[fixture.SPACE])
            packet=FramePacket('A','camera',1,'now',1.,np.zeros((100,100,3),np.uint8))
            with self.assertRaises(ValueError):save_capture(Path(d),packet,roi,'b','s',fixture.CFG)
    def test_bad_manifest_does_not_abort_good_csv(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.record(root);(root/'manifests'/'broken.json').write_text('{',encoding='utf-8')
            path=export_csv(root);self.assertIn('A-01',path.read_text(encoding='utf-8-sig'))
    def test_bad_analysis_preserves_original_in_csv(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root);(root/'analysis').mkdir();(root/'analysis'/path.name).write_text('{',encoding='utf-8')
            output=export_csv(root);self.assertIn(record['image_id'],output.read_text(encoding='utf-8-sig'))
    def test_review_rejects_sticker_from_other_slot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root);rows=default_review(record)
            rows[0].update(plate_text='12',color='white',sticker_polygon=[[0,0],[9,0],[9,9],[0,9]],review_status='approved')
            with self.assertRaises(ValueError):save_review(root,record,rows,'tester')
    def test_review_rejects_wrong_record_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root);record['lot_id']='B'
            with self.assertRaises(ValueError):save_review(root,record,default_review(record),'tester')
    def test_bom_camera_config_and_relative_replay_path(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'cameras.json';p.write_text(json.dumps({'cameras':{'A':{'source':'file','file_path':'a.mp4'},'B':{'source':'file','file_path':'b.mp4'}}}),encoding='utf-8-sig')
            specs=load_camera_specs(p);self.assertEqual(specs[0].file_path,str((root/'a.mp4').resolve()))
    def test_camera_config_does_not_truncate_float_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cameras.json';p.write_text(json.dumps({'width':640.5,'cameras':{'A':{'serial':'a'},'B':{'serial':'b'}}}),encoding='utf-8')
            with self.assertRaises(ValueError):load_camera_specs(p)
    def test_successful_retry_removes_old_error(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root);record.update(analysis_status='error',analysis_error='out of memory')
            from parking_ai.storage import atomic_json
            atomic_json(path,record)
            worker=Analyzer(root,root,fixture.CFG);worker.reader=fixture.Reader();worker.engine={};worker.process(path)
            self.assertNotIn('analysis_error',read_json(path))


    def test_missing_or_damaged_result_requires_reanalysis(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root)
            from contracts import CONTRACT_REVISION
            record.update(analysis_status='complete',analysis_revision=CONTRACT_REVISION)
            result=root/'analysis'/path.name
            self.assertTrue(needs_analysis(record,result_path=result))
            result.parent.mkdir();result.write_text('{}',encoding='utf-8')
            self.assertTrue(needs_analysis(record,result_path=result))

    def test_explicit_retry_once_without_hot_error_loop(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.record(root)
            from parking_ai.storage import atomic_json
            record.update(analysis_status='error');atomic_json(path,record)
            worker=Analyzer(root,root,fixture.CFG);cycles=[]
            def next_cycle(timeout):
                cycles.append(1)
                if len(cycles)==1:worker.request_retry_errors()
                if len(cycles)==3:worker.stop_event.set()
            with patch('pipeline.make_reader',return_value=(fixture.Reader(),{'device':'cpu'})),patch.object(worker,'process') as process,patch.object(worker.stop_event,'wait',side_effect=next_cycle):
                worker.run()
            self.assertEqual(process.call_count,1)

    def test_failure_status_write_cannot_stop_other_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.record(root);self.record(root);worker=Analyzer(root,root,fixture.CFG)
            calls=[]
            def process(path):
                calls.append(path)
                if len(calls)==1:raise RuntimeError('inference failed')
                worker.stop_event.set()
            with patch('pipeline.make_reader',return_value=(fixture.Reader(),{'device':'cpu'})),patch.object(worker,'process',side_effect=process),patch('pipeline.atomic_json',side_effect=OSError('disk full')),patch('pipeline.logging.exception'):
                worker.run()
            self.assertEqual(len(calls),2)

    def test_ui_draining_latest_queue_cannot_fail_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,_=self.record(root);worker=Analyzer(root,root,fixture.CFG)
            worker.reader=fixture.Reader();worker.engine={}
            with patch.object(worker.latest,'put_nowait',side_effect=[queue.Full,None]),patch.object(worker.latest,'get_nowait',side_effect=queue.Empty):worker.process(path)
            self.assertEqual(read_json(path)['analysis_status'],'complete')

    def test_stale_review_cannot_overwrite_new_review(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root);rows=default_review(record)
            first=save_review(root,record,rows,'first',expected_revision=None)
            second=save_review(root,record,rows,'second',expected_revision=review_revision(first))
            with self.assertRaises(ValueError):save_review(root,record,rows,'stale',expected_revision=review_revision(first))
            self.assertEqual(read_json(root/'reviews'/(record['image_id']+'.json')),second)
            self.assertEqual(len(list((root/'reviews'/'history').glob('*.json'))),1)

    def test_invalid_number_can_be_draft_but_not_approved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root);rows=default_review(record);rows[0]['plate_text']='1'
            saved=save_review(root,record,rows,'tester');row=saved['spaces'][0]
            self.assertIsNone(row['car_id']);self.assertEqual(row['number_status'],'invalid_number')
            self.assertEqual(export_reviewed(root)[1],0)
            rows[0]['review_status']='approved'
            with self.assertRaises(ValueError):save_review(root,record,rows,'tester')

    def test_duplicate_approved_physical_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.record(root)
            record['roi']['spaces']=[{'space_id':'A-01','space_type':'general','polygon':[[0,0],[40,0],[40,90],[0,90]]},
                {'space_id':'A-02','space_type':'general','polygon':[[50,0],[99,0],[99,90],[50,90]]}]
            rows=default_review(record)
            for row in rows:row.update(plate_text='12',occupancy='occupied',review_status='approved')
            with self.assertRaises(ValueError):save_review(root,record,rows,'tester')

    def test_bad_candidate_geometry_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,_=self.record(root);worker=Analyzer(root,root,fixture.CFG);worker.reader=fixture.Reader();worker.engine={};worker.process(path)
            result=root/'analysis'/path.name;data=read_json(result)
            data['spaces'][0]['candidates']=[{'text':'12','status':'candidate','text_bbox':[1]}]
            from parking_ai.storage import atomic_json
            atomic_json(result,data)
            self.assertIn('invalid_analysis',export_csv(root).read_text(encoding='utf-8-sig'))
            self.assertEqual(len(read_json(root/'candidates.report.json')['errors']),1)

    def test_nonfinite_bool_and_wrong_type_settings_rejected(self):
        from settings import validate_config
        cfg=read_json(Path(__file__).resolve().parents[1]/'config.json')
        for key,value in [('save_interval',float('nan')),('warmup_seconds',True),('download_models','false'),('ocr_min_score',2),('ocr_languages','en')]:
            with self.subTest(key=key):
                modified=copy.deepcopy(cfg);modified[key]=value
                with self.assertRaises(ValueError):validate_config(modified,Path.cwd())

if __name__=='__main__':unittest.main()

