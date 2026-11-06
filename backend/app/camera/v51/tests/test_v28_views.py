"""Viewpoint metadata, portable folders, group isolation and UI transitions."""
import copy,gc,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from test_v19 import fixture,PROJECT
from capture_views import VIEW_NAMES,view_code,view_folder,capture_pose
from dataset19 import persist_scene,rebuild_scene_assets,DatasetIndex,export_training
from collection_output import export_collection,read_collection
from review import save_review,rows_for_edit,export_reviewed
from records import validate_record
from pipeline import read_json
from parking_ai.storage import atomic_json
from vision19 import VisionEngine
from dataset_audit import build_audit,render_report


class ViewpointData(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.packet,self.roi=fixture()
    def tearDown(self):self.temp.cleanup()
    def save(self,view,pred=False):
        cfg={**self.cfg,'capture_view':view};p=replace(self.packet,sequence=list(VIEW_NAMES).index(view)+1)
        prediction=VisionEngine().run(p,self.roi,cfg) if pred else None
        return persist_scene(self.root,p,self.roi,'same-vehicle-arrangement','session',cfg,prediction)[0]
    def approve(self,r):
        rows=rows_for_edit(r)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        save_review(self.root,r,rows,'fixture truth')
    def test_five_views_in_distinct_korean_bay_folders_and_json(self):
        for view,name in VIEW_NAMES.items():
            if view=='unspecified':continue
            r=self.save(view);bundle=read_collection(self.root,r)
            self.assertIn('/'+name+'/',r['raw_path'])
            self.assertEqual(r['analysis_config']['capture_view'],view)
            for item in bundle['items']:
                self.assertIn('/'+name+'/',item['path'])
                if item['kind']=='korean_label':
                    value=read_json(self.root/item['path']);self.assertEqual(value['촬영시점'],name)
                    self.assertEqual(value['촬영시점코드'],view);self.assertEqual(value['촬영배치'],'same-vehicle-arrangement')
        report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],5)
        self.assertTrue(all(report['views'][v]['장면수']==1 for v in VIEW_NAMES if v!='unspecified'))
    def test_view_path_cannot_escape_dataset(self):
        for view in ('../outside','../../','right/left',None,42):
            with self.subTest(view=view),self.assertRaises(ValueError):
                persist_scene(self.root,self.packet,self.roi,'b','s',{**self.cfg,'capture_view':view})
        self.assertFalse((self.root/'raw').exists())
    def test_record_view_must_match_capture_config(self):
        r=self.save('right');r['capture_view']='left'
        with self.assertRaises(ValueError):validate_record(r,self.root)
    def test_different_view_same_frame_reserves_different_id(self):
        first=persist_scene(self.root,self.packet,self.roi,'b','s',{**self.cfg,'capture_view':'right'})[0]
        second=persist_scene(self.root,self.packet,self.roi,'b','s',{**self.cfg,'capture_view':'left'})[0]
        self.assertNotEqual(first['image_id'],second['image_id'])
    def test_cannot_overwrite_existing_capture_with_other_view(self):
        r=self.save('right')
        with self.assertRaises(ValueError):persist_scene(self.root,self.packet,self.roi,'b','s',{**self.cfg,'capture_view':'left'},existing=r['image_id'])
    def test_repair_keeps_stored_view_and_original_path(self):
        r=self.save('ceiling');before=(self.root/r['raw_path']).read_bytes()
        result,receipt=rebuild_scene_assets(self.root,r['image_id'],{**self.cfg,'capture_view':'left'})
        self.assertEqual(result['capture_view'],'ceiling');self.assertEqual(receipt['status'],'complete')
        self.assertEqual((self.root/r['raw_path']).read_bytes(),before)
    def test_auto_export_preserves_view_paths_and_reference_links(self):
        self.save('upper');self.save('lower');target,report=export_collection(self.root);target=Path(target)
        self.assertEqual({r['capture_view'] for r in report['scenes']},{'upper','lower'})
        for p in (target/'collected').rglob('*.json'):
            label=read_json(p)
            for field in ('표시사진파일','원본사진파일','한글라벨파일'):self.assertTrue((target/label[field]).is_file())
    def test_training_keeps_all_views_of_same_arrangement_in_one_split(self):
        for view in ('right','left','upper','lower','ceiling'):self.approve(self.save(view))
        target,count=export_training(self.root);report=read_json(Path(target)/'manifest.json')
        self.assertEqual(count,10);self.assertEqual(len({s['split'] for s in report['samples']}),1)
        self.assertEqual({s['capture_view'] for s in report['samples']},set(VIEW_NAMES)-{'unspecified'})
        self.assertEqual(set(report['groups']),{'same-vehicle-arrangement'})
    def test_reviewed_export_view_folders_are_portable(self):
        self.approve(self.save('ceiling'));target,count=export_reviewed(self.root)
        self.assertEqual(count,2)
        for line in target.read_text(encoding='utf-8').splitlines():
            row=json.loads(line);self.assertEqual(row['capture_view'],'ceiling');self.assertIn('/천장/',row['raw_path'])
            self.assertTrue((target.parent/row['raw_path']).is_file())
    def test_per_view_audit_null_without_truth_and_counts_abstention(self):
        self.approve(self.save('right',True));self.save('left',True)
        report=build_audit(self.root);self.assertEqual(report['views']['right']['metrics']['occupancy']['compared'],2)
        self.assertEqual(report['views']['right']['metrics']['occupancy']['abstained'],2)
        self.assertEqual(report['views']['right']['metrics']['occupancy']['accuracy_including_abstention'],0)
        self.assertIsNone(report['views']['left']['metrics']['occupancy']['accuracy_including_abstention'])
        self.assertIsNone(report['views']['ceiling']['metrics']['number']['accuracy_including_abstention'])
        self.assertIn('촬영 시점별 정답 대조',render_report(report))
    def test_per_view_missing_prediction_counted_separately(self):
        self.approve(self.save('lower'));report=build_audit(self.root)
        v=report['views']['lower'];self.assertEqual(v['unmatched_truth_counts']['occupancy'],2)
        self.assertIsNone(v['metrics']['occupancy']['accuracy_including_abstention'])
    def test_pose_and_saved_anchor_separate_by_view(self):
        from automatic_setup import anchor_path,save_anchor,try_anchor
        from layouts import snapshot
        paths=[]
        for view in ('right','left','ceiling'):
            pose=capture_pose({**self.cfg,'capture_view':view});roi=snapshot('A','A',self.packet.serial,[360,300],pose,self.roi['spaces'])
            save_anchor(self.root,self.packet,roi);paths.append(anchor_path(self.root,'A','A',pose))
        self.assertEqual(len(set(paths)),3);self.assertTrue(all(p.exists() for p in paths))
    def test_legacy_missing_view_is_not_guessed(self):
        self.assertEqual(view_code({}),'unspecified');self.assertEqual(view_folder({}),'')
        self.assertEqual(capture_pose(self.cfg),self.cfg['camera_pose']['A'])


class ViewpointUI(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.window=tk.Tk();self.window.withdraw()
        self.cfg,self.p,self.roi=fixture();self.app=Dashboard(self.window,self.cfg,Path(self.temp.name),PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.saving=False;self.app.close();self.window.mainloop()
        self.app=None;self.window=None;gc.collect();self.temp.cleanup()
    def test_view_switch_keeps_group_and_invalidates_geometry(self):
        a=self.app;group=a.batch;session=a.session;a.roi=self.roi;a.raw=self.p;a.view.set('오른쪽')
        self.assertTrue(a.change_view());self.assertEqual(a.batch,group);self.assertEqual(a.session,session)
        self.assertEqual(a.cfg['capture_view'],'right');self.assertIsNone(a.roi);self.assertIsNone(a.raw)
    def test_ceiling_is_default_and_first_option(self):
        self.assertEqual(self.app.view.get(),'천장');self.assertEqual(self.app.cfg['capture_view'],'ceiling')
        self.assertEqual(next(iter(VIEW_NAMES)),'ceiling')
    def test_scope_change_clears_stale_view_metrics(self):
        a=self.app;a.view_audit.set('old 100%');a.scope.set('현재 주차장');a.refresh_stats()
        self.assertNotIn('100%',a.view_audit.get());self.assertIn('다시 실행',a.view_audit.get())
    def test_recovery_guard_rolls_back_view_and_lot_widgets(self):
        a=self.app;a.recovering=True;a.view.set('왼쪽');a.lot.set('B');a.guard(a.change_view)
        self.assertEqual(a.view.get(),'천장');self.assertEqual(a.lot.get(),'A');self.assertTrue(a.recovering)
        a.recovering=False
    def test_live_view_switch_waits_and_reregisters(self):
        a=self.app;a.auto_run=True;a.input_mode='camera';a.view.set('왼쪽');a.change_view()
        self.assertTrue(a.auto_run);self.assertEqual(a.auto_phase,'waiting');self.assertEqual(a.active_view,'left')
        self.assertIn('__view-left',capture_pose(a.cfg))
    def test_busy_view_change_rolls_back_selection(self):
        a=self.app;a.saving=True;a.view.set('아래쪽')
        with self.assertRaises(ValueError):a.change_view()
        self.assertEqual(a.view.get(),'천장');self.assertEqual(a.cfg['capture_view'],'ceiling')
    def test_stop_does_not_erase_recovery_state_or_diagnostics(self):
        a=self.app;a.recovering=True;a.recovery_result={'복구보류수':1};a.stop_live()
        self.assertTrue(a.recovering);self.assertEqual(a.recovery_result['복구보류수'],1);a.recovering=False
    def test_cancel_unsaved_changes_keeps_view(self):
        a=self.app;a.dirty=True;a.view.set('위쪽')
        with patch('dashboard.messagebox.askyesno',return_value=False):self.assertFalse(a.change_view())
        self.assertEqual(a.view.get(),'천장')


class SelectedTraining(unittest.TestCase):
    def setUp(self):
        from test_v27 import full_fixture
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi=full_fixture();self.cfg['capture_view']='ceiling'
    def tearDown(self):self.temp.cleanup()
    def approved(self,packet):
        r=persist_scene(self.root,packet,self.roi,'same-arrangement','s',self.cfg)[0]
        rows=rows_for_edit(r)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        save_review(self.root,r,rows,'fixture truth');return r
    def test_only_passed_approved_crops_exported(self):
        self.approved(self.p);blur=self.p.image.copy();blur[:]=175
        self.approved(replace(self.p,image=blur,sequence=self.p.sequence+1))
        target,count=export_training(self.root,selected_only=True);report=read_json(Path(target)/'manifest.json')
        self.assertEqual(count,12);self.assertEqual(len(report['skipped']),1);self.assertTrue(report['selected_only'])
        self.assertEqual(export_training(self.root)[1],24)
    def test_old_unassessed_review_excluded(self):
        r=self.approved(self.p);path=self.root/'bundles'/(r['image_id']+'.json');bundle=read_json(path)
        bundle.pop('quality_selection_sha256');atomic_json(path,bundle)
        target,count=export_training(self.root,selected_only=True);self.assertEqual(count,0)
        self.assertIn('미판정',read_json(Path(target)/'manifest.json')['skipped'][0]['reason'])
    def test_automatic_pass_cannot_become_training_truth(self):
        from vision19 import make_reference
        pred=VisionEngine().run(self.p,self.roi,self.cfg,make_reference(self.p,self.roi))
        persist_scene(self.root,self.p,self.roi,'b','s',self.cfg,pred)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['quality_passed'],1)
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)
    def test_ui_worker_uses_selected_training(self):
        from dashboard import Worker
        worker=Worker()
        with patch('dashboard.export_training',return_value=('test-target',0)) as export:
            worker.start()
            try:
                worker.jobs.put(('export',None,(self.root,None,None)))
                result=worker.results.get(timeout=10);self.assertIsNone(result[3])
                self.assertTrue(export.call_args.kwargs['selected_only'])
            finally:worker.jobs.put(None);worker.join(10)
