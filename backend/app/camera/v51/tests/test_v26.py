"""Interrupted evidence promises, immutable export hashes and capture binding."""
import copy,hashlib,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from test_v19 import fixture
from dataset19 import persist_scene,rebuild_scene_assets,DatasetIndex
from vision19 import VisionEngine
from pipeline import read_json
from review import rows_for_edit,save_review,export_reviewed
from collection_output import export_collection,read_collection
from export_integrity import ExportTransaction
from parking_ai.storage import atomic_json


class CaptureBinding(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.packet,self.roi=fixture()
        self.record,_=persist_scene(self.root,self.packet,self.roi,'batch','session',self.cfg)
        self.manifest=self.root/'manifests'/(self.record['image_id']+'.json')
        self.bundle=self.root/'bundles'/self.manifest.name
    def tearDown(self):self.temp.cleanup()
    def approve(self):
        rows=rows_for_edit(self.record);rows[0].update(occupancy='vacant',review_status='approved')
        return save_review(self.root,self.record,rows,'tester')
    def test_review_rejects_stale_geometry_before_any_write(self):
        current=copy.deepcopy(self.record);current['roi']['spaces'][0]['polygon'][0][0]+=3
        atomic_json(self.manifest,current);before=self.bundle.read_bytes()
        with self.assertRaises(ValueError):self.approve()
        self.assertFalse((self.root/'reviews'/self.manifest.name).exists())
        self.assertEqual(self.bundle.read_bytes(),before)
    def test_review_rejects_stale_capture_metadata(self):
        for field,value in [('captured_at_utc','2026-11-04T10:00:00+00:00'),('batch_id','changed'),('session_id','changed'),('source',{'kind':'import'})]:
            current=copy.deepcopy(self.record);current[field]=value;atomic_json(self.manifest,current)
            with self.subTest(field=field),self.assertRaises(ValueError):self.approve()
    def test_review_accepts_only_completion_status_refresh(self):
        current=copy.deepcopy(self.record);current['collection_status']='partial';atomic_json(self.manifest,current)
        result=self.approve();self.assertEqual(result['spaces'][0]['review_status'],'approved')
        self.assertEqual(read_json(self.manifest)['collection_status'],'complete')
    def test_export_rejects_sidecars_from_different_capture_metadata(self):
        for field,value in [('captured_at_utc','2026-11-04T10:00:00+00:00'),('batch_id','changed'),('session_id','changed')]:
            current=copy.deepcopy(self.record);current[field]=value;atomic_json(self.manifest,current)
            with self.subTest(field=field):self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_index_and_export_detect_stale_roi_without_prediction(self):
        current=copy.deepcopy(self.record);current['roi']['spaces'][0]['polygon'][0][0]+=3;atomic_json(self.manifest,current)
        index=DatasetIndex().scan(self.root,force=True)
        self.assertEqual(index['counts']['partial'],1)
        self.assertTrue(index['errors'])
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_wrong_photo_reference_is_rejected_even_when_json_digest_matches(self):
        bundle=read_json(self.bundle);item=next(i for i in bundle['items'] if i['kind']=='korean_label')
        path=self.root/item['path'];label=read_json(path);label['표시사진파일']='missing.png';atomic_json(path,label)
        item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();atomic_json(self.bundle,bundle)
        with self.assertRaises(ValueError):read_collection(self.root,self.record)
    def test_complete_bundle_with_errors_is_not_exported(self):
        bundle=read_json(self.bundle);bundle['errors']=[{'error':'missing evidence'}];atomic_json(self.bundle,bundle)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_scene_and_per_bay_json_must_contain_same_results(self):
        bundle=read_json(self.bundle);item=next(i for i in bundle['items'] if i['kind']=='scene_label')
        path=self.root/item['path'];label=read_json(path);label['주차칸별결과'][0]['점유상태']='빈칸';atomic_json(path,label)
        item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();atomic_json(self.bundle,bundle)
        with self.assertRaises(ValueError):read_collection(self.root,self.record)
        index=DatasetIndex().scan(self.root,force=True)
        self.assertEqual(index['counts']['partial'],1);self.assertTrue(index['errors'])
    def test_invalid_bundle_type_skips_only_bad_scene(self):
        p=replace(self.packet,sequence=2,timestamp_utc='2026-11-04T09:00:01+00:00')
        persist_scene(self.root,p,self.roi,'batch','session',self.cfg);atomic_json(self.bundle,[])
        target,result=export_collection(self.root)
        self.assertEqual(result['counts']['scenes'],1);self.assertEqual(len(result['skipped']),1)
        self.assertEqual(read_json(Path(target)/'EXPORT_STATUS.json')['상태'],'완료')


class EvidencePromise(unittest.TestCase):
    def test_first_manifest_records_prediction_promise_before_asset_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg,packet,roi=fixture();pred=VisionEngine().run(packet,roi,cfg)
            import pipeline
            real=pipeline.atomic_json
            def stop(path,data):real(path,data);raise SystemExit('after first manifest')
            with patch('pipeline.atomic_json',side_effect=stop):
                with self.assertRaises(SystemExit):persist_scene(root,packet,roi,'batch','session',cfg,pred)
            record=read_json(next((root/'manifests').glob('*.json')))
            with self.assertRaises(ValueError):rebuild_scene_assets(root,record['image_id'],cfg)
            self.assertEqual(read_json(root/'manifests'/(record['image_id']+'.json'))['prediction_status'],'pending')
            restored,receipt=persist_scene(root,packet,roi,'batch','session',cfg,pred)
            self.assertEqual(restored['image_id'],record['image_id']);self.assertEqual(receipt['status'],'complete')
            self.assertEqual(DatasetIndex().scan(root)['counts']['originals'],1)


class ImmutableExports(unittest.TestCase):
    def test_verified_path_cannot_be_reregistered_with_new_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(ValueError):
                with ExportTransaction(root) as transfer:
                    path=root/'data.bin';path.write_bytes(b'original');transfer.verify('data.bin',hashlib.sha256(b'original').hexdigest())
                    path.write_bytes(b'changed');transfer.verify('data.bin',hashlib.sha256(b'changed').hexdigest());transfer.finish()
            self.assertEqual(read_json(root/'EXPORT_STATUS.json')['상태'],'실패')
    def test_same_hash_can_be_reverified(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with ExportTransaction(root) as transfer:
                path=root/'data.bin';path.write_bytes(b'original');digest=hashlib.sha256(b'original').hexdigest()
                transfer.verify('data.bin',digest);transfer.verify('data.bin',digest);transfer.finish()
            self.assertEqual(read_json(root/'EXPORT_STATUS.json')['상태'],'완료')
    def test_review_export_detects_original_changed_during_grouping(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg,packet,roi=fixture();record,_=persist_scene(root,packet,roi,'batch','session',cfg)
            rows=rows_for_edit(record);rows[0].update(occupancy='vacant',review_status='approved');save_review(root,record,rows,'tester')
            original=(root/record['raw_path']).read_bytes()
            import multi_vehicle
            real=multi_vehicle.group_reviewed
            def corrupt(source,target):
                result=real(source,target)
                next((Path(target).parent/'reviewed.images'/'originals').glob('*.png')).write_bytes(b'bad')
                return result
            with patch('multi_vehicle.group_reviewed',side_effect=corrupt):
                with self.assertRaises(ValueError):export_reviewed(root)
            self.assertEqual((root/record['raw_path']).read_bytes(),original)
