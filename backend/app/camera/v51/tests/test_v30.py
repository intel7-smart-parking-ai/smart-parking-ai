"""Number-pixel quality, shared sticker evidence and current-policy exports."""
import copy,gc,hashlib,json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import cv2
from test_v19 import PROJECT
from test_v27 import full_fixture
from quality_selection import evaluate
from dataset19 import persist_scene,DatasetIndex,export_training,rebuild_scene_assets
from collection_output import read_collection,export_collection
from review import rows_for_edit,save_review
from pipeline import read_json


class NumberPixels(unittest.TestCase):
    def setUp(self):
        from test_v27 import ScreeningRules
        case=ScreeningRules();case.setUp();case.occupied()
        self.record=case.record;self.image=case.image;self.labels=case.labels
    def result(self):return evaluate(self.record,self.image,self.labels,{'predictions':None,'reviews':None})
    def test_flat_number_inside_sharp_sticker_is_held(self):
        self.image[70:100,60:90]=175
        result=self.result();self.assertEqual(result['상태'],'보류')
        self.assertIn('번호 영역 선명도 부족',result['칸별결과'][0]['사유'])
    def test_number_reflection_is_held(self):
        self.image[70:100,60:90]=255
        self.assertIn('번호 영역 과도하게 밝음',self.result()['칸별결과'][0]['사유'])
    def test_number_underexposure_is_held(self):
        self.image[70:100,60:90]=0
        self.assertIn('번호 영역 과도하게 어두움',self.result()['칸별결과'][0]['사유'])
    def test_blur_of_number_only_is_held(self):
        self.image[70:100,60:90]=cv2.GaussianBlur(self.image[70:100,60:90],(17,17),5)
        self.assertIn('번호 영역 선명도 부족',self.result()['칸별결과'][0]['사유'])
    def test_textured_number_retains_pass(self):self.assertEqual(self.result()['상태'],'통과')
    def test_number_metrics_exclude_surrounding_sharp_edges(self):
        self.image[70:100,60:90]=175
        value=self.result()['칸별결과'][0]['측정값']['번호']
        self.assertEqual((value['너비_px'],value['높이_px']),(30,30));self.assertEqual(value['선명도'],0)
        self.assertGreater(value['유효픽셀수'],0)
    def test_disjoint_number_and_sticker_in_same_bay_is_held(self):
        self.labels[0]['스티커꼭짓점_px']=[[100,60],[160,60],[160,120],[100,120]]
        self.assertIn('번호와 스티커 영역 불일치',self.result()['칸별결과'][0]['사유'])
    def test_sixty_percent_number_overlap_is_accepted(self):
        self.labels[0]['스티커꼭짓점_px']=[[72,60],[132,60],[132,120],[72,120]]
        self.assertEqual(self.result()['상태'],'통과')
    def test_less_than_sixty_percent_number_overlap_is_held(self):
        self.labels[0]['스티커꼭짓점_px']=[[73,60],[133,60],[133,120],[73,120]]
        self.assertIn('번호와 스티커 영역 불일치',self.result()['칸별결과'][0]['사유'])


class CurrentQuality(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=full_fixture()
    def tearDown(self):self.temp.cleanup()
    def save(self,legacy=False,occupied=False,flat=False):
        image=self.p.image.copy()
        if flat:image[70:100,60:90]=175
        packet=replace(self.p,image=image)
        record=persist_scene(self.root,packet,self.roi,'batch','session',self.cfg)[0];rows=rows_for_edit(record)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        if occupied:rows[0].update(occupancy='occupied',plate_text='23',color='green',plate_bbox=[60,70,90,100],sticker_polygon=[[50,60],[110,60],[110,120],[50,120]])
        def old_evaluate(*args,**kwargs):
            result=evaluate(*args,**kwargs);result.pop('검사개정',None);return result
        if legacy:
            with patch('collection_output.evaluate',side_effect=old_evaluate):save_review(self.root,record,rows,'tester')
        else:save_review(self.root,record,rows,'tester')
        return record
    def test_old_pass_is_unassessed_in_current_ui_counts(self):
        self.save(legacy=True);counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['quality_unassessed'],1);self.assertEqual(counts['selected_occupancy_ready'],0)
        self.assertEqual(counts['quality_recheck_required'],1)
    def test_old_pass_remains_readable_and_all_candidate_exportable(self):
        record=self.save(legacy=True)
        self.assertEqual(read_collection(self.root,record)['status'],'complete')
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],1)
    def test_old_pass_excluded_from_selected_collection(self):
        self.save(legacy=True);report=export_collection(self.root,selected_only=True)[1]
        self.assertEqual(report['counts']['scenes'],0);self.assertIn('품질 재검사 필요',report['skipped'][0]['reason'])
    def test_old_pass_excluded_from_selected_training(self):
        self.save(legacy=True);self.assertEqual(export_training(self.root,selected_only=True)[1],0)
    def test_repair_upgrades_old_gate_without_changing_raw(self):
        record=self.save(legacy=True);raw=self.root/record['raw_path'];before=hashlib.sha256(raw.read_bytes()).hexdigest()
        rebuild_scene_assets(self.root,record['image_id'],self.cfg)
        self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),before)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['quality_passed'],1)
        self.assertEqual(export_training(self.root,selected_only=True)[1],12)
    def test_current_pass_exports_and_counts_exact_tasks(self):
        record=self.save(occupied=True);selection=read_json(self.root/'selection'/(record['image_id']+'.json'))
        self.assertEqual(selection['검사개정'],'number-pixels-v1')
        counts=DatasetIndex().scan(self.root)['counts'];self.assertEqual(counts['selected_number_ready'],1)
        self.assertEqual(export_training(self.root,selected_only=True)[1],14)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],1)
    def test_blurred_approved_number_excluded_from_both_selected_exports(self):
        self.save(occupied=True,flat=True);counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['quality_held'],1);self.assertEqual(counts['selected_number_ready'],0)
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
    def test_legacy_policy_keys_are_not_rewritten(self):
        self.cfg['quality_selection'].pop('min_number_short_side',None);record=self.save()
        self.assertNotIn('min_number_short_side',read_json(self.root/'selection'/(record['image_id']+'.json'))['정책'])
        self.assertEqual(export_training(self.root,selected_only=True)[1],12)


class QualityUI(unittest.TestCase):
    def test_old_pass_is_shown_as_needing_quality_recheck(self):
        import tkinter as tk
        from dashboard import Dashboard
        case=CurrentQuality();case.setUp();window=None;app=None
        try:
            case.save(legacy=True);window=tk.Tk();window.withdraw();app=Dashboard(window,case.cfg,case.root,PROJECT)
            result=DatasetIndex().scan(case.root)
            token=(str(case.root),app.scope.get(),app.lot.get() if app.scope.get()=='현재 주차장' else None,app.session if app.scope.get()=='현재 세션' else None)
            app.accept('scan',token,result,None)
            self.assertIn('통과 0 / 보류 0 / 미판정 1',app.storage.get())
            self.assertIn('품질 통과·승인 학습 가능 0/0/0',app.storage.get())
            self.assertIn('이전 기준 재검사 1장',app.storage.get())
        finally:
            if app:app.dirty=False;app.close();window.mainloop()
            app=None;window=None;gc.collect();case.tearDown()


class WholeFlow(unittest.TestCase):
    def test_a_b_all_five_views_save_review_select_and_train(self):
        from capture_views import capture_pose
        from layouts import snapshot
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for lot in ('A','B'):
                for view in ('ceiling','right','left','upper','lower'):
                    with self.subTest(lot=lot,view=view):
                        cfg,p,roi=full_fixture(lot);cfg['capture_view']=view
                        roi=snapshot(lot,p.camera_id,p.serial,list(p.image.shape[1::-1]),capture_pose(cfg),roi['spaces'])
                        record=persist_scene(root,p,roi,'same-arrangement','session',cfg)[0];rows=rows_for_edit(record)
                        for row in rows:row.update(occupancy='vacant',review_status='approved')
                        cx,cy=np.mean(roi['spaces'][0]['polygon'],axis=0).astype(int).tolist()
                        rows[0].update(occupancy='occupied',plate_text='23',color='green',plate_bbox=[cx-12,cy-12,cx+12,cy+12],sticker_polygon=[[cx-22,cy-22],[cx+22,cy-22],[cx+22,cy+22],[cx-22,cy+22]])
                        save_review(root,record,rows,'synthetic integration fixture')
            index=DatasetIndex().scan(root)
            self.assertEqual(index['counts']['quality_passed'],10);self.assertEqual(index['counts']['selected_number_ready'],10)
            self.assertTrue(all(v['통과']==2 for k,v in index['views'].items() if k!='unspecified'))
            target,report=export_collection(root,selected_only=True)
            self.assertEqual(report['counts'],{'scenes':10,'space_pairs':140,'images':160,'json_files':150,'originals':10})
            self.assertEqual(read_json(Path(target)/'EXPORT_STATUS.json')['상태'],'완료')
            target,count=export_training(root,selected_only=True);self.assertEqual(count,160)
            manifest=read_json(Path(target)/'manifest.json')
            self.assertEqual(len({s['split'] for s in manifest['samples']}),1)
            self.assertEqual({s['capture_view'] for s in manifest['samples']},{'ceiling','right','left','upper','lower'})
