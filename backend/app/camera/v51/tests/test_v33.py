"""Capture identity, input provenance and training split isolation (offline)."""
import copy
from dataclasses import replace
import gc,hashlib,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import cv2
import tkinter as tk
from test_v19 import fixture,PROJECT
from test_v27 import full_fixture
from collection_output import export_collection
from dashboard import Dashboard
from dataset19 import DatasetIndex,capture_id,export_training,observation_key,persist_scene,rebuild_scene_assets
from parking_ai.storage import atomic_json
from pipeline import read_json
from records import capture_source
from review import rows_for_edit,save_review


def input_hashes(root):
    return {p.relative_to(root):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*')
            if p.is_file() and 'exports' not in p.relative_to(root).parts}


class CaptureIdentity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cfg,self.p,self.roi=fixture()
    def save(self,batch='old-batch',session='old-session',existing=None):
        return persist_scene(self.root,self.p,self.roi,batch,session,self.cfg,existing=existing)[0]
    def test_same_frame_old_uuid_is_stable(self):
        r=self.save();self.assertEqual(r['image_id'],'c524733c501f5894aa33cb77e9cfeefb')
        self.assertEqual(self.save()['image_id'],r['image_id'])
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['capture_scenes'],1)
    def test_wrong_existing_group_rejected_before_any_change(self):
        r=self.save();rows=rows_for_edit(r);rows[0].update(occupancy='vacant',review_status='approved')
        save_review(self.root,r,rows,'reviewer');before=input_hashes(self.root)
        for b,s in [('new','old-session'),('old-batch','new'),('new','new')]:
            with self.subTest(batch=b,session=s),self.assertRaisesRegex(ValueError,'배치/세션'):self.save(b,s,r['image_id'])
            self.assertEqual(before,input_hashes(self.root))
    def test_new_group_fresh_frame_survives_export_and_session_filter(self):
        a=self.save();self.p=replace(self.p,sequence=2);b=self.save('new-batch','new-session')
        self.assertNotEqual(a['image_id'],b['image_id'])
        target,report=export_collection(self.root,session='new-session')
        self.assertEqual(report['counts']['scenes'],1);self.assertEqual(report['scenes'][0]['image_id'],b['image_id'])
        rows=[read_json(p) for p in (Path(target)/'collected').rglob('*.json')]
        self.assertTrue(rows);self.assertTrue(all((r['촬영배치'],r['촬영세션'])==('new-batch','new-session') for r in rows))
    def test_stale_import_details_cannot_mislabel_camera_or_break_retry(self):
        old={'kind':'import','original_filename':'previous.jpg','sha256':'a'*64}
        self.cfg['capture_source']=old.copy();r=self.save();self.assertEqual(r['source'],{'kind':'camera'})
        self.assertEqual(r['analysis_config']['capture_source'],r['source']);self.assertEqual(self.cfg['capture_source'],old)
        self.assertEqual(self.save()['image_id'],r['image_id'])
        self.assertEqual(rebuild_scene_assets(self.root,r['image_id'],self.cfg)[1]['status'],'complete')
        target,report=export_collection(self.root);self.assertEqual(report['counts']['scenes'],1)
        row=read_json(next((Path(target)/'collected').rglob('*.json')));self.assertEqual(row['입력출처'],'실제 카메라')
    def test_stale_camera_details_discarded_on_video(self):
        self.p=replace(self.p,source_kind='video');self.cfg['capture_source']={'kind':'camera','captured_time_kind':'host_receive_utc'}
        self.assertEqual(self.save()['source'],{'kind':'video'})
    def test_matching_photo_provenance_and_legacy_import_retry(self):
        self.p=replace(self.p,camera_id='IMPORT');self.roi['camera_id']='IMPORT'
        from layouts import snapshot
        self.roi=snapshot('A','IMPORT',self.p.serial,self.roi['image_size'],self.roi['pose'],self.roi['spaces'])
        self.cfg['capture_source']={'kind':'import','original_filename':'주차장.jpg','sha256':'a'*64}
        r=self.save();self.assertEqual(r['source'],self.cfg['capture_source'])
        self.assertEqual(self.save()['image_id'],r['image_id'])
    def test_explicit_legacy_virtual_photo_is_never_promoted_to_real_photo(self):
        p=replace(self.p,camera_id='IMPORT',source_kind='virtual')
        self.assertEqual(capture_source(p,{'capture_source':{'kind':'camera'}}),{'kind':'virtual'})
    def test_existing_id_cannot_select_a_manifest_outside_dataset(self):
        with self.assertRaisesRegex(ValueError,'사진 ID'):self.save(existing='../outside')


class DashboardGrouping(unittest.TestCase):
    def test_new_batch_disarms_old_photo_and_accepts_new_observation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg,p,roi=full_fixture();window=tk.Tk();window.withdraw();app=Dashboard(window,cfg,Path(d),PROJECT)
            try:
                app.input_mode='image';app.roi=roi;app.raw=p;app.scene=(p,roi);app.confirmed_epoch=p.stream_epoch
                old=app.batch;app.new_batch();self.assertNotEqual(old,app.batch)
                with self.assertRaisesRegex(ValueError,'사진'):app.get_packet()
                with self.assertRaisesRegex(ValueError,'새 촬영 배치'):app.check_ready(p)
                new=replace(p,sequence=p.sequence+1,received_monotonic=time.monotonic()+.001)
                app.raw=new;app.save_scene()
                end=time.monotonic()+15
                while app.saving and time.monotonic()<end:window.update();time.sleep(.01)
                self.assertFalse(app.saving);self.assertEqual(app.record['batch_id'],app.batch);self.assertEqual(app.record['frame_id'],new.sequence)
                self.assertEqual(len(list((app.root/'manifests').glob('*.json'))),1)
            finally:app.dirty=False;app.close();window.mainloop();del app,window;gc.collect()
    def test_old_save_receipt_cannot_attach_to_next_frame_or_group(self):
        cfg,p,roi=fixture();b='b';s='s';root=Path('test')
        token=(1,str(root),capture_id(p,roi,b,s,cfg))
        record={'image_id':'saved','raw_path':'raw/saved.png'}
        for q,group in [(replace(p,sequence=2),(b,s)),(replace(p,stream_epoch=2),(b,s)),(p,('other',s))]:
            app=SimpleNamespace(saving=True,root=root,cfg=cfg,scene=(q,roi),scene_group=group,generation=1,record=None,
                                notice=Mock(),refresh_stats=Mock(),rows=[])
            Dashboard.accept(app,'save',token,(record,{'status':'complete','errors':[]}),None)
            self.assertIsNone(app.record);app.refresh_stats.assert_called_once()
    def test_late_number_result_with_same_time_not_sent_for_new_frame(self):
        cfg,p,roi=fixture();app=SimpleNamespace(generation=1,latest_occupancy_key=observation_key(replace(p,sequence=2),roi),
                                                 send_prediction=Mock(),publish_scene=Mock())
        Dashboard.accept(app,'identify',(1,p,roi,None),{'some':'prediction'},None)
        app.send_prediction.assert_not_called();app.publish_scene.assert_called_once()


class PixelSplitIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi=full_fixture()
    def approved(self,other_split,change_pixel=False,legacy_hash=False):
        def train(g):return int(hashlib.sha256(g.encode()).hexdigest()[:8],16)%100<70
        groups=['first',next('group'+str(i) for i in range(120) if train('group'+str(i))!=train('first')) if other_split else 'first']
        for i,group in enumerate(groups):
            image=self.p.image.copy()
            if i and change_pixel:image[:,:,0]^=1
            p=replace(self.p,image=image,sequence=i+1)
            r,_=persist_scene(self.root,p,self.roi,group,'session',self.cfg)
            if i:
                payload=cv2.imencode('.png',image,[cv2.IMWRITE_PNG_COMPRESSION,9])[1].tobytes();(self.root/r['raw_path']).write_bytes(payload)
                if legacy_hash:r.pop('raw_sha256')
                else:
                    digest=hashlib.sha256(payload).hexdigest();self.assertNotEqual(r['raw_sha256'],digest);r['raw_sha256']=digest
                atomic_json(self.root/'manifests'/(r['image_id']+'.json'),r)
            rows=rows_for_edit(r)
            for row in rows:row.update(occupancy='vacant',review_status='approved')
            save_review(self.root,r,rows,'test')
    def test_same_pixels_across_splits_fail_both_exports_without_changing_source(self):
        self.approved(True);before=input_hashes(self.root)
        for selected in (True,False):
            with self.assertRaisesRegex(ValueError,'동일 원본 분할 누수'):export_training(self.root,selected_only=selected)
        for target in (self.root/'exports').iterdir():
            self.assertEqual(read_json(target/'EXPORT_STATUS.json')['상태'],'실패');self.assertFalse((target/'manifest.json').exists())
        self.assertEqual(before,input_hashes(self.root))
    def test_legacy_record_without_encoded_hash_still_protects_split(self):
        self.approved(True,legacy_hash=True)
        with self.assertRaisesRegex(ValueError,'동일 원본 분할 누수'):export_training(self.root)
    def test_same_pixels_within_one_group_remain_exportable(self):
        self.approved(False);target,n=export_training(self.root,selected_only=True)
        samples=read_json(Path(target)/'manifest.json')['samples'];self.assertEqual(n,24)
        self.assertEqual(len({s['raw_pixels_sha256'] for s in samples}),1);self.assertEqual(len({s['raw_sha256'] for s in samples}),2)
    def test_distinct_decoded_images_may_occupy_different_splits(self):
        self.approved(True,change_pixel=True);target,n=export_training(self.root,selected_only=True)
        samples=read_json(Path(target)/'manifest.json')['samples'];self.assertEqual(n,24)
        self.assertEqual(len({s['split'] for s in samples}),2);self.assertEqual(len({s['raw_pixels_sha256'] for s in samples}),2)
