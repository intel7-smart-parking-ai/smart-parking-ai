"""Quality gates and selected export with generated fixtures, not field accuracy."""
import copy,hashlib,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
import cv2,numpy as np
from test_v19 import fixture
from demo import build_demo_scene
from pipeline import read_json
from dataset19 import persist_scene,DatasetIndex,rebuild_scene_assets
from review import rows_for_edit,save_review
from collection_output import labels_for_scene,export_collection,read_collection,input_fingerprints
from quality_selection import evaluate,measure,policy_for,load_selection
from parking_ai.storage import atomic_json


def full_fixture(lot='A',source='import'):
    cfg,_,_=fixture();packet,roi,_,_=build_demo_scene(lot,cfg)
    image=np.random.default_rng(270).integers(45,236,packet.image.shape,dtype=np.uint8)
    return cfg,replace(packet,image=image,source_kind=source),roi


class ScreeningRules(unittest.TestCase):
    def setUp(self):
        cfg,p,roi=full_fixture();self.image=p.image
        self.record={'image_id':'unit','lot_id':'A','lot_name':'가','roi':roi,'source':{'kind':'import'},
          'analysis_config':cfg,'image_size':list(p.image.shape[1::-1]),'frame_id':1,'batch_id':'b',
          'session_id':'s','captured_at_utc':p.timestamp_utc,'raw_path':'raw/unit.png','raw_sha256':'0'*64}
        self.labels=labels_for_scene(self.record)
        for r in self.labels:r.update(점유상태='빈칸')
    def run_gate(self):return evaluate(self.record,self.image,self.labels,{'predictions':None,'reviews':None})
    def occupied(self):
        self.labels[0].update(점유상태='차량 있음',차량번호표기='23',스티커색='초록',
            번호영역_xyxy_px=[60,70,90,100],스티커꼭짓점_px=[[50,60],[110,60],[110,120],[50,120]])
    def test_complete_textured_empty_scene_passes_without_truth_approval(self):
        result=self.run_gate();self.assertEqual(result['상태'],'통과');self.assertEqual(result['통과칸수'],12)
        self.assertTrue(all(not r['사람검수완료'] for r in self.labels))
    def test_occupied_number_color_and_regions_pass(self):
        self.occupied();self.assertEqual(self.run_gate()['상태'],'통과')
    def test_all_bays_required(self):
        self.labels.pop();self.assertIn('전체 주차칸 등록 누락',self.run_gate()['보류사유'])
    def test_unknown_occupancy_held(self):
        self.labels[0]['점유상태']='관측 보류';self.assertEqual(self.run_gate()['보류칸수'],1)
    def test_missing_identity_evidence_held(self):
        self.occupied()
        for key,value in [('차량번호표기',None),('스티커색','미확인'),('번호영역_xyxy_px',None),('스티커꼭짓점_px',None)]:
            before=copy.deepcopy(self.labels[0]);self.labels[0][key]=value
            with self.subTest(key=key):self.assertEqual(self.run_gate()['상태'],'보류')
            self.labels[0]=before
    def test_d39_color_mismatch_held(self):
        self.occupied();self.labels[0]['스티커색']='파랑'
        self.assertIn('번호와 스티커색 불일치',self.run_gate()['칸별결과'][0]['사유'])
    def test_wrong_zone_is_valid_training_candidate(self):
        self.occupied();self.labels[0]['전용칸종류비교']='종류 불일치 의심'
        self.assertEqual(self.run_gate()['상태'],'통과')
    def test_duplicate_number_held(self):
        self.occupied();self.labels[1].update(점유상태='차량 있음',차량번호표기='23',스티커색='초록')
        self.assertIn('장면 내 번호 중복',self.run_gate()['칸별결과'][0]['사유'])
    def test_vacant_conflicting_identity_held(self):
        self.labels[0]['차량번호표기']='23';self.assertEqual(self.run_gate()['보류칸수'],1)
    def test_synthetic_and_video_never_quality_pass(self):
        for kind in ('synthetic','video'):
            self.record['source']['kind']=kind
            with self.subTest(kind=kind):self.assertEqual(self.run_gate()['상태'],'보류')
    def test_flat_dark_bright_and_blurred_held(self):
        for value in (0,175,255):
            self.image.fill(value)
            with self.subTest(value=value):self.assertEqual(self.run_gate()['보류칸수'],12)
    def test_outside_white_padding_not_measured(self):
        a=np.full((100,100,3),100,np.uint8);poly=[[40,10],[60,10],[80,90],[20,90]]
        mask=np.zeros((100,100),np.uint8);cv2.fillPoly(mask,[np.array(poly)],255);a[mask==0]=255
        result=measure(a,poly);self.assertEqual(result['밝은픽셀비율'],0);self.assertEqual(result['선명도'],0)
    def test_sticker_small_resolution_held(self):
        self.occupied();self.labels[0]['스티커꼭짓점_px']=[[50,60],[59,60],[59,69],[50,69]]
        self.assertIn('스티커 해상도 부족',self.run_gate()['칸별결과'][0]['사유'])
    def test_invalid_policy_rejected(self):
        for value in ({'no_such_key':1},{'min_sharpness':float('nan')},{'min_sharpness':True},
                      {'max_dark_ratio':2},{'min_bay_short_side':0},{'min_bay_short_side':32.5},None):
            with self.subTest(value=value),self.assertRaises(ValueError):policy_for({'quality_selection':value})
    def test_policy_snapshot_changes_decision(self):
        self.record['analysis_config']['quality_selection']['min_bay_short_side']=300
        self.assertEqual(self.run_gate()['보류칸수'],12)


class SelectionPersistence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.packet,self.roi=full_fixture()
        self.record,_=persist_scene(self.root,self.packet,self.roi,'b','s',self.cfg)
        self.path=self.root/'selection'/(self.record['image_id']+'.json')
    def tearDown(self):self.temp.cleanup()
    def approve_empty(self):
        rows=rows_for_edit(self.record)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        save_review(self.root,self.record,rows,'generated-fixture tester')
    def test_unknown_saved_as_held_not_io_failure(self):
        result=DatasetIndex().scan(self.root)
        self.assertEqual(result['counts']['quality_held'],1);self.assertFalse(result['errors'])
        self.assertEqual(read_json(self.path)['보류칸수'],12)
    def test_review_recomputes_gate_without_new_original(self):
        self.approve_empty();index=DatasetIndex().scan(self.root)
        self.assertEqual(index['counts']['originals'],1);self.assertEqual(index['counts']['quality_passed'],1)
    def test_selected_export_excludes_hold_and_matches_counts(self):
        self.approve_empty()
        persist_scene(self.root,replace(self.packet,sequence=self.packet.sequence+1),self.roi,'b','s',self.cfg)
        index=DatasetIndex().scan(self.root);target,report=export_collection(self.root,selected_only=True)
        self.assertEqual(report['counts']['scenes'],index['counts']['quality_passed'])
        self.assertEqual(report['counts']['space_pairs'],12);self.assertEqual(len(report['skipped']),1)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],2)
        target=Path(target);status=read_json(target/'EXPORT_STATUS.json');self.assertEqual(status['상태'],'완료')
        for item in status['파일목록']:self.assertEqual(hashlib.sha256((target/item['파일']).read_bytes()).hexdigest(),item['SHA256'])
        for label in (target/'collected').rglob('*.json'):
            data=read_json(label);self.assertEqual(data['품질선별']['장면상태'],'통과')
            self.assertTrue((target/data['원본사진파일']).exists());self.assertTrue((target/data['표시사진파일']).exists())
    def test_quality_record_tamper_invalidates_cached_pass(self):
        self.approve_empty();index=DatasetIndex();self.assertEqual(index.scan(self.root)['counts']['quality_passed'],1)
        value=read_json(self.path);value['상태']='보류';atomic_json(self.path,value)
        result=index.scan(self.root);self.assertEqual(result['counts']['quality_passed'],0);self.assertTrue(result['errors'])
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
    def test_changed_review_cannot_reuse_stale_pass(self):
        self.approve_empty();path=self.root/'reviews'/self.path.name;data=read_json(path);data['reviewer']='changed';atomic_json(path,data)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
    def test_old_bundle_unassessed_can_export_all_but_not_selected(self):
        self.approve_empty();path=self.root/'bundles'/self.path.name;data=read_json(path)
        data.pop('quality_selection_sha256');data.pop('quality_selection_status');atomic_json(path,data)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],1)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['quality_unassessed'],1)
    def test_policy_edit_cannot_reuse_old_decision(self):
        self.approve_empty();path=self.root/'manifests'/self.path.name;record=read_json(path)
        record['analysis_config']['quality_selection']['min_sharpness']=99999;atomic_json(path,record)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
    def test_label_selection_must_match_even_after_item_hash_rewritten(self):
        self.approve_empty();path=self.root/'bundles'/self.path.name;receipt=read_json(path)
        item=next(i for i in receipt['items'] if i['kind']=='korean_label');label_path=self.root/item['path']
        label=read_json(label_path);label['품질선별']['상태']='보류';atomic_json(label_path,label)
        item['sha256']=hashlib.sha256(label_path.read_bytes()).hexdigest();atomic_json(path,receipt)
        with self.assertRaises(ValueError):read_collection(self.root,self.record)
    def test_repair_keeps_capture_policy_not_new_config(self):
        self.approve_empty();modified=copy.deepcopy(self.cfg);modified['quality_selection']['min_sharpness']=99999
        rebuild_scene_assets(self.root,self.record['image_id'],modified)
        self.assertEqual(read_json(self.path)['정책'],self.cfg['quality_selection']);self.assertEqual(read_json(self.path)['상태'],'통과')
    def test_b16_full_empty_review_and_export(self):
        cfg,p,roi=full_fixture('B');r,_=persist_scene(self.root,p,roi,'b','s',cfg)
        rows=rows_for_edit(r)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        save_review(self.root,r,rows,'generated-fixture tester')
        result=export_collection(self.root,lot='B',selected_only=True)[1]
        self.assertEqual(result['counts']['scenes'],1);self.assertEqual(result['counts']['space_pairs'],16)


class SelectionUI(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from dashboard import Dashboard
        from test_v19 import PROJECT
        self.temp=tempfile.TemporaryDirectory();self.window=tk.Tk();self.window.withdraw()
        cfg,_,_=fixture();self.app=Dashboard(self.window,cfg,Path(self.temp.name),PROJECT)
    def tearDown(self):
        import gc
        self.app.dirty=False;self.app.close();self.window.mainloop();self.temp.cleanup()
        self.app=None;self.window=None;gc.collect()
    def test_selected_export_routes_and_failure_releases_lock(self):
        from unittest.mock import patch
        with patch.object(self.app,'submit') as submit:
            self.app.export_current('selected_export')
            self.assertEqual(submit.call_args.args[0],'selected_export')
            with self.assertRaises(ValueError):self.app.export_current('collection_export')
        self.app.accept('selected_export',None,None,'test failure');self.assertFalse(self.app.exporting)
        self.assertIn('완료 수량으로 집계하지 않음',self.app.export_status.get())
    def test_worker_selected_export_completion_and_count_ui(self):
        from dashboard import Worker
        worker=Worker();worker.start()
        try:
            worker.jobs.put(('selected_export',None,(Path(self.temp.name),None,None)))
            kind,token,result,error=worker.results.get(timeout=10);self.assertIsNone(error)
            self.app.exporting=True;self.app.accept(kind,token,result,error)
            self.assertFalse(self.app.exporting);self.assertIn('장면 0',self.app.export_status.get())
        finally:worker.jobs.put(None);worker.join(10)
