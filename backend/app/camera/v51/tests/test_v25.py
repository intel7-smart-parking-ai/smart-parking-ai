"""Interrupted initial capture and portable, verified export regressions."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from test_v19 import fixture
from dataset19 import persist_scene,export_training,DatasetIndex
from collection_output import export_collection
from review import save_review,rows_for_edit,export_reviewed
from pipeline import read_json
from parking_ai.storage import atomic_json


class InitialCapture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.packet,self.roi=fixture()
    def tearDown(self):self.temp.cleanup()
    def save(self,packet=None):return persist_scene(self.root,packet or self.packet,self.roi,'batch','session',self.cfg)
    def test_retry_after_raw_before_manifest_reuses_one_original(self):
        with patch('pipeline.atomic_json',side_effect=SystemExit('power loss after raw')):
            with self.assertRaises(SystemExit):self.save()
        record,receipt=self.save()
        self.assertEqual(receipt['status'],'complete')
        self.assertEqual(len(list((self.root/'raw').rglob('*.png'))),1)
        self.assertEqual(len(list((self.root/'manifests').glob('*.json'))),1)
    def test_retry_after_manifest_before_assets_reuses_same_capture(self):
        with patch('dataset19.atomic_json',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):self.save()
        first=list((self.root/'manifests').glob('*.json'))[0].stem
        record,receipt=self.save()
        self.assertEqual(record['image_id'],first)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['originals'],1)
    def test_retry_cannot_replace_pixels_of_interrupted_raw(self):
        with patch('pipeline.atomic_json',side_effect=SystemExit):
            with self.assertRaises(SystemExit):self.save()
        raw=list((self.root/'raw').rglob('*.png'))[0];original=raw.read_bytes()
        pixels=self.packet.image.copy();pixels[0,0]=0
        with self.assertRaises(ValueError):self.save(replace(self.packet,image=pixels))
        self.assertEqual(raw.read_bytes(),original)
    def test_different_frame_creates_new_capture(self):
        first,_=self.save();second,_=self.save(replace(self.packet,sequence=2,timestamp_utc='2026-11-04T09:00:01+00:00'))
        self.assertNotEqual(first['image_id'],second['image_id'])
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['originals'],2)
    def test_resave_rejects_other_camera_serial_and_source(self):
        record,_=self.save();before=(self.root/'manifests'/(record['image_id']+'.json')).read_bytes()
        for changes in ({'camera_id':'B'},{'serial':'other'},{'source_kind':'import'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                persist_scene(self.root,replace(self.packet,**changes),self.roi,'batch','session',self.cfg,existing=record['image_id'])
        self.assertEqual((self.root/'manifests'/(record['image_id']+'.json')).read_bytes(),before)


class Exports(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.packet,self.roi=fixture()
        self.record,_=persist_scene(self.root,self.packet,self.roi,'batch','session',self.cfg)
        rows=rows_for_edit(self.record);rows[0].update(occupancy='vacant',review_status='approved')
        save_review(self.root,self.record,rows,'tester')
    def tearDown(self):self.temp.cleanup()
    def test_review_export_raw_paths_are_portable(self):
        target,count=export_reviewed(self.root)
        self.assertEqual(count,1)
        row=json.loads(target.read_text(encoding='utf-8').splitlines()[0])
        exported=target.parent/row['raw_path']
        self.assertTrue(exported.is_file(),row['raw_path'])
        self.assertEqual(hashlib.sha256(exported.read_bytes()).hexdigest(),row['raw_sha256'])
        frame=json.loads(target.with_suffix('.frames.jsonl').read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(frame['raw_path'],row['raw_path'])
    def test_review_export_has_one_transfer_folder(self):
        target,_=export_reviewed(self.root)
        self.assertNotEqual(target.parent,self.root/'exports')
        self.assertEqual(read_json(target.parent/'EXPORT_STATUS.json')['상태'],'완료')
    def test_automatic_export_interruption_leaves_pending_status(self):
        with patch('collection_output.atomic_bytes',side_effect=SystemExit('interrupted copy')):
            with self.assertRaises(SystemExit):export_collection(self.root)
        folders=list((self.root/'exports').glob('automatic-*'));self.assertEqual(len(folders),1)
        self.assertEqual(read_json(folders[0]/'EXPORT_STATUS.json')['상태'],'진행 중')
        self.assertFalse((folders[0]/'내보내기_결과.json').exists())
    def test_automatic_export_verifies_written_bytes(self):
        import collection_output
        real=collection_output.atomic_bytes;corrupted=[]
        def wrong(path,payload):
            digest=real(path,payload)
            if Path(path).suffix=='.png' and not corrupted:
                Path(path).write_bytes(b'bad');corrupted.append(str(path))
            return digest
        with patch('collection_output.atomic_bytes',side_effect=wrong):
            with self.assertRaises(ValueError):export_collection(self.root)
        target=list((self.root/'exports').glob('automatic-*'))[0]
        self.assertEqual(read_json(target/'EXPORT_STATUS.json')['상태'],'실패')
    def test_training_final_manifest_failure_is_marked_failed(self):
        import dataset19
        real=dataset19.atomic_json
        def fail(path,data):
            if Path(path).name=='manifest.json':raise OSError('disk full at final manifest')
            return real(path,data)
        with patch('dataset19.atomic_json',side_effect=fail):
            with self.assertRaises(OSError):export_training(self.root)
        target=list((self.root/'exports').glob('training-*'))[0]
        self.assertTrue((target/'FAILED.json').is_file())
        self.assertEqual(read_json(target/'EXPORT_STATUS.json')['상태'],'실패')
    def test_training_bad_review_does_not_cancel_valid_scenes(self):
        atomic_json(self.root/'reviews'/'broken.json',[])
        target,count=export_training(self.root)
        self.assertEqual(count,1)
        self.assertTrue(any(r['file']=='broken.json' for r in read_json(Path(target)/'manifest.json')['skipped']))
    def test_training_missing_manifest_skips_only_that_review(self):
        extra=read_json(self.root/'reviews'/(self.record['image_id']+'.json'))
        atomic_json(self.root/'reviews'/'missing.json',extra)
        target,count=export_training(self.root)
        self.assertEqual(count,1)
        self.assertTrue(any(r['file']=='missing.json' for r in read_json(Path(target)/'manifest.json')['skipped']))
    def test_training_write_failure_aborts_instead_of_input_skip(self):
        with patch('dataset19.write_png',side_effect=ValueError('output encoder failed')):
            with self.assertRaises(ValueError):export_training(self.root)
        target=list((self.root/'exports').glob('training-*'))[0]
        self.assertEqual(read_json(target/'EXPORT_STATUS.json')['상태'],'실패')
    def test_training_silent_output_corruption_is_rejected(self):
        import dataset19
        real=dataset19.write_png
        def bad(path,image):
            digest=real(path,image);Path(path).write_bytes(b'bad');return digest
        with patch('dataset19.write_png',side_effect=bad):
            with self.assertRaises(ValueError):export_training(self.root)
    def test_completed_exports_have_verified_file_inventory(self):
        automatic,_=export_collection(self.root);training,_=export_training(self.root)
        reviewed,_=export_reviewed(self.root)
        for target in (Path(automatic),Path(training),reviewed.parent):
            with self.subTest(folder=target.name):
                status=read_json(target/'EXPORT_STATUS.json');self.assertEqual(status['상태'],'완료')
                files={p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file() and p.name!='EXPORT_STATUS.json'}
                self.assertEqual(files,{r['파일'] for r in status['파일목록']})
                for row in status['파일목록']:
                    self.assertEqual(hashlib.sha256((target/row['파일']).read_bytes()).hexdigest(),row['SHA256'])
