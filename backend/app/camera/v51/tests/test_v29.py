"""Final review: actual number-crop sizes, eligibility counts and unknown views."""
import copy,errno,gc,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from test_v19 import fixture,PROJECT
from test_v27 import full_fixture
from quality_selection import evaluate,policy_for
from dataset19 import persist_scene,DatasetIndex,export_training
from review import rows_for_edit,save_review
from collection_output import read_collection
from pipeline import read_json


class TransientLock(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        self.handle=Mock();self.handle.fileno.return_value=1
        self.target='msvcrt.locking' if os.name=='nt' else 'fcntl.flock'
    def test_transient_busy_retries_before_protected_work(self):
        from dataset_lock import acquire_os_lock
        with patch(self.target,side_effect=[OSError(errno.EACCES,'busy'),OSError(errno.EAGAIN,'busy'),None]) as lock,patch('dataset_lock.time.sleep') as sleep:
            acquire_os_lock(self.handle)
            self.assertEqual(lock.call_count,3);self.assertEqual([c.args[0] for c in sleep.call_args_list],[.01,.04])
    def test_persistent_busy_is_bounded_and_never_acquired(self):
        from dataset_lock import acquire_os_lock
        with patch(self.target,side_effect=OSError(errno.EACCES,'busy')) as lock,patch('dataset_lock.time.sleep') as sleep:
            with self.assertRaises(OSError):acquire_os_lock(self.handle)
            self.assertEqual(lock.call_count,5);self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list),.35)
    def test_unexpected_os_error_is_not_retried(self):
        from dataset_lock import acquire_os_lock
        with patch(self.target,side_effect=OSError(errno.EIO,'io')) as lock,patch('dataset_lock.time.sleep') as sleep:
            with self.assertRaises(OSError):acquire_os_lock(self.handle)
            self.assertEqual(lock.call_count,1);sleep.assert_not_called()


class NumberCropQuality(unittest.TestCase):
    def setUp(self):
        from test_v27 import ScreeningRules
        fixture_case=ScreeningRules();fixture_case.setUp();fixture_case.occupied()
        self.record=fixture_case.record;self.image=fixture_case.image;self.labels=fixture_case.labels
    def gate(self):return evaluate(self.record,self.image,self.labels,{'predictions':None,'reviews':None})
    def test_one_pixel_number_crop_must_hold_scene(self):
        self.labels[0]['번호영역_xyxy_px']=[60,70,61,71]
        result=self.gate();self.assertEqual(result['상태'],'보류');self.assertIn('번호 영역 해상도 부족',result['칸별결과'][0]['사유'])
    def test_integer_export_crop_size_used_at_threshold(self):
        self.labels[0]['번호영역_xyxy_px']=[60.9,70.9,76.8,86.8]
        result=self.gate();self.assertEqual(result['상태'],'통과')
        self.assertEqual(result['칸별결과'][0]['측정값']['번호']['짧은변_px'],16)
    def test_fractional_box_collapsing_to_zero_is_held(self):
        self.labels[0]['번호영역_xyxy_px']=[60.1,70.1,60.9,70.9]
        self.assertEqual(self.gate()['상태'],'보류')
    def test_readable_size_retains_pass(self):self.assertEqual(self.gate()['상태'],'통과')
    def test_explicit_number_crop_policy_is_honored(self):
        self.record['analysis_config']['quality_selection']['min_number_short_side']=32
        self.assertEqual(self.gate()['상태'],'보류')
    def test_invalid_number_policy_rejected(self):
        for value in (True,1,16.5,float('nan')):
            with self.subTest(value=value),self.assertRaises(ValueError):policy_for({'quality_selection':{'min_number_short_side':value}})


class SelectedCounts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi=full_fixture()
    def tearDown(self):self.temp.cleanup()
    def approve(self,p):
        r=persist_scene(self.root,p,self.roi,'batch','session',self.cfg)[0];rows=rows_for_edit(r)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        save_review(self.root,r,rows,'tester');return r
    def test_quality_held_approvals_are_not_selected_training_ready(self):
        image=self.p.image.copy();image[:]=175;self.approve(replace(self.p,image=image))
        counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['occupancy_ready'],12)
        self.assertEqual(counts.get('selected_occupancy_ready'),0)
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)
    def test_mixed_scene_selected_counts_equal_export_samples(self):
        self.approve(self.p);image=self.p.image.copy();image[:]=175
        self.approve(replace(self.p,image=image,sequence=self.p.sequence+1))
        counts=DatasetIndex().scan(self.root)['counts'];target,n=export_training(self.root,selected_only=True)
        self.assertEqual(sum(counts.get('selected_'+t+'_ready',-1) for t in ('occupancy','number','color')),n)
        self.assertEqual(n,12)
    def test_legacy_five_key_policy_remains_readable(self):
        self.cfg['quality_selection'].pop('min_number_short_side',None)
        r=self.approve(self.p)
        policy=read_json(self.root/'selection'/(r['image_id']+'.json'))['정책']
        self.assertNotIn('min_number_short_side',policy)
        self.assertEqual(read_collection(self.root,r)['status'],'complete')
        self.assertEqual(export_training(self.root,selected_only=True)[1],12)
    def test_old_tiny_number_pass_rejected_until_repair(self):
        r=persist_scene(self.root,self.p,self.roi,'batch','session',self.cfg)[0];rows=rows_for_edit(r)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        rows[0].update(occupancy='occupied',plate_text='23',color='green',plate_bbox=[60,70,61,71],sticker_polygon=[[50,60],[110,60],[110,120],[50,120]])
        with patch('quality_selection.number_crop_reasons',return_value=[]):save_review(self.root,r,rows,'tester')
        with self.assertRaises(ValueError):read_collection(self.root,r)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['selected_number_ready'],0)
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)
        from dataset19 import rebuild_scene_assets
        rebuild_scene_assets(self.root,r['image_id'],self.cfg)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['quality_held'],1)


class UnknownViewUI(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=fixture()
        self.record=persist_scene(self.root,self.p,self.roi,'b','s',self.cfg)[0]
        self.window=tk.Tk();self.window.withdraw();self.app=Dashboard(self.window,self.cfg,self.root,PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.close();self.window.mainloop();self.app=None;self.window=None;gc.collect();self.temp.cleanup()
    def load(self):self.app.accept('load',self.app.generation,(self.record,self.p.image,None,None),None)
    def test_unknown_saved_view_does_not_appear_as_ceiling(self):
        self.load();self.assertEqual(self.app.view.get(),'미지정');self.assertEqual(self.app.active_view,'unspecified')
    def test_unknown_saved_view_requires_choice_before_new_capture(self):
        self.load()
        with patch.object(self.app,'connect') as connect:
            with self.assertRaises(ValueError):self.app.start_automatic()
            connect.assert_not_called()
    def test_unknown_view_blocks_import_before_file_dialog(self):
        self.load()
        with patch('dashboard.filedialog.askopenfilename') as choose:
            with self.assertRaises(ValueError):self.app.import_image()
            choose.assert_not_called()
    def test_ui_selected_count_matches_quality_held_data(self):
        counts=DatasetIndex().scan(self.root)
        token=(str(self.root),self.app.scope.get(),self.app.lot.get() if self.app.scope.get()=='현재 주차장' else None,self.app.session if self.app.scope.get()=='현재 세션' else None)
        self.app.accept('scan',token,counts,None)
        self.assertIn('품질 통과·승인 학습 가능 0/0/0',self.app.storage.get())
    def test_selecting_ceiling_after_unknown_invalidates_old_geometry(self):
        self.load();self.app.view.set('천장');self.assertTrue(self.app.change_view())
        self.assertIsNone(self.app.roi);self.assertEqual(self.app.cfg['capture_view'],'ceiling')
    def test_fresh_start_still_defaults_to_ceiling(self):self.assertEqual(self.app.view.get(),'천장')
