"""V21 correctness and requirement regressions; no camera/model/live network."""
import copy,gc,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch,MagicMock
import tkinter as tk
import cv2,numpy as np
from test_v19 import fixture,PROJECT
from test_v20 import sticker_packet
from vision19 import VisionEngine,make_reference,independent_sticker
from sticker_ocr import read_sticker,rectified_views,overlap_fraction
from prediction_contract import validate_prediction
from dataset19 import persist_scene
from dataset_audit import build_audit,export_audit
from review import rows_for_edit,save_review
from pipeline import read_json
from parking_ai.storage import atomic_json


class Reader:
    def __init__(self,answers=None):self.answers=list(answers or []);self.calls=0
    def readtext(self,pixels,**kwargs):
        self.calls+=1;answer=self.answers.pop(0) if self.answers else '23'
        if isinstance(answer,Exception):raise answer
        if answer is None:return []
        h,w=pixels.shape[:2];box=[[w*.35,h*.4],[w*.65,h*.4],[w*.65,h*.6],[w*.35,h*.6]]
        return [(box,answer,.98)]


def record_context(p,roi):
    return {'roi':roi,'frame_id':p.sequence,'captured_at_utc':p.timestamp_utc,'lot_id':roi['lot_id'],
            'stream_epoch':p.stream_epoch,'image_size':list(p.image.shape[1::-1])}


class IdentityEvidence(unittest.TestCase):
    def setUp(self):self.cfg,self.p,self.roi,self.ref=sticker_packet()
    def full(self,reader=None):
        engine=VisionEngine();engine.reader=reader or Reader()
        return engine.enrich(self.p,self.roi,self.cfg,engine.run(self.p,self.roi,self.cfg,self.ref))
    def test_round_sticker_supported(self):
        for color,bgr in [('green',(0,200,0)),('blue',(200,50,0)),('white',(240,240,240))]:
            with self.subTest(color=color):
                image=self.p.image.copy();image[45:220,35:135]=45
                cv2.circle(image,(85,105),30,bgr,-1);cv2.putText(image,'23',(67,115),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,0),2)
                poly,actual=independent_sticker(image,self.roi['spaces'][0]['polygon'])
                self.assertEqual(actual,color);self.assertIsNotNone(poly)
    def test_same_sticker_foreground_and_background_hole_are_not_two_objects(self):
        cfg,p,roi=fixture();cv2.rectangle(p.image,(45,75),(125,130),(200,50,0),-1)
        cv2.putText(p.image,'23',(60,115),cv2.FONT_HERSHEY_SIMPLEX,.85,(0,0,0),2)
        self.assertEqual(independent_sticker(p.image,roi['spaces'][0]['polygon'])[1],'blue')
    def test_two_or_more_preprocessings_agree(self):
        full=self.full();row=full['spaces'][0]
        self.assertEqual(row['number'],'23');self.assertEqual(row['color'],'green')
        self.assertIn(row['selected_ocr'],row['ocr_candidates']);validate_prediction(full,record_context(self.p,self.roi))
    def test_conflicting_valid_numbers_are_not_guessed(self):
        row=self.full(Reader(['23','42','23']))['spaces'][0]
        self.assertIsNone(row['number']);self.assertEqual(row['ocr_status'],'conflicting_views');self.assertEqual(row['color'],'green')
    def test_one_view_only_is_held(self):
        row=self.full(Reader(['23']+[None]*8))['spaces'][0]
        self.assertIsNone(row['number']);self.assertEqual(row['ocr_status'],'single_view_unconfirmed')
    def test_invalid_rotation_does_not_hide_valid_next_orientation(self):
        row=self.full(Reader(['8','23','23','23']))['spaces'][0]
        self.assertEqual(row['number'],'23');self.assertTrue(any(c['rotation_degrees']==90 and c['text']=='23' for c in row['ocr_candidates']))
    def test_obsolete_number_never_converted_to_operating_number(self):
        row=self.full(Reader(['49']*12))['spaces'][0]
        self.assertIsNone(row['number']);self.assertTrue(all(c['number_candidate'] is None for c in row['ocr_candidates']))
    def test_evidence_coordinates_return_to_original(self):
        row=self.full()['spaces'][0];a,b,c,d=row['plate_bbox']
        self.assertTrue(48<=a<c<=121 and 75<=b<d<=129)
        self.assertGreaterEqual(overlap_fraction(row['plate_bbox'],row['sticker_polygon']),.6)
    def test_neighbor_slot_evidence_rejected(self):
        pred=self.full();pred['spaces'][0]['sticker_polygon']=[[210,60],[270,60],[280,140],[220,140]]
        with self.assertRaises(ValueError):validate_prediction(pred,record_context(self.p,self.roi))
    def test_number_with_error_status_rejected(self):
        pred=self.full();pred['spaces'][0]['ocr_status']='error'
        with self.assertRaises(ValueError):validate_prediction(pred,record_context(self.p,self.roi))
    def test_selected_number_must_match_direct_ocr(self):
        pred=self.full();pred['spaces'][0]['number']='42'
        with self.assertRaises(ValueError):validate_prediction(pred,record_context(self.p,self.roi))
    def test_missing_agreement_rejected(self):
        pred=self.full();row=pred['spaces'][0];row['ocr_candidates']=[row['selected_ocr']]
        with self.assertRaises(ValueError):validate_prediction(pred,record_context(self.p,self.roi))
    def test_late_wrong_frame_cannot_be_enriched(self):
        engine=VisionEngine();pred=engine.run(self.p,self.roi,self.cfg,self.ref)
        with self.assertRaises(ValueError):engine.enrich(replace(self.p,sequence=9),self.roi,self.cfg,pred)
    def test_no_local_sticker_does_not_claim_floor_digits(self):
        cfg,p,roi=fixture();engine=VisionEngine();engine.reader=Reader()
        pred=engine.run(p,roi,cfg,None,'manual')
        with patch('vision19.sticker_region',return_value=(None,'unknown')):full=engine.enrich(p,roi,cfg,pred)
        self.assertTrue(all(r['number'] is None for r in full['spaces']))
    def test_one_slot_ocr_error_does_not_discard_other_slots(self):
        image=self.p.image.copy();cv2.rectangle(image,(225,75),(275,125),(200,50,0),-1)
        cv2.putText(image,'23',(228,110),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,0),2)
        p=replace(self.p,image=image);engine=VisionEngine();engine.reader=Reader()
        fast=engine.run(p,self.roi,self.cfg,None,'manual');self.assertEqual(fast['spaces'][1]['color'],'blue')
        def localized(image,poly,reader,cfg):
            if min(x for x,y in poly)<150:raise RuntimeError('first-slot-only')
            return read_sticker(image,poly,reader,cfg)
        with patch('sticker_ocr.read_sticker',side_effect=localized):full=engine.enrich(p,self.roi,self.cfg,fast)
        self.assertIsNone(full['spaces'][0]['number']);self.assertEqual(full['spaces'][1]['number'],'23');self.assertTrue(full['errors'])
    def test_duplicate_numbers_are_both_held(self):
        image=self.p.image.copy();cv2.rectangle(image,(225,75),(275,125),(200,50,0),-1)
        cv2.putText(image,'23',(228,110),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,0),2)
        p=replace(self.p,image=image);engine=VisionEngine();engine.reader=Reader()
        full=engine.enrich(p,self.roi,self.cfg,engine.run(p,self.roi,self.cfg,None,'manual'))
        self.assertTrue(all(r['number'] is None for r in full['spaces']))
        self.assertTrue(all(r['ocr_status']=='duplicate_number' for r in full['spaces']))


class IntegrityAndAudit(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi,self.ref=sticker_packet();self.pred=VisionEngine().run(self.p,self.roi,self.cfg,self.ref)
        self.record,_=persist_scene(self.root,self.p,self.roi,'same-batch','session',self.cfg,self.pred)
    def tearDown(self):self.temp.cleanup()
    def approve(self):
        rows=rows_for_edit(self.record);rows[0].update(occupancy='occupied',plate_text='23',color='green',
            plate_bbox=[55,90,95,115],sticker_polygon=self.pred['spaces'][0]['sticker_polygon'],review_status='approved')
        save_review(self.root,self.record,rows,'tester')
    def test_different_epoch_cannot_resave(self):
        with self.assertRaisesRegex(ValueError,'연결 구간'):persist_scene(self.root,replace(self.p,stream_epoch=7),self.roi,self.record['batch_id'],self.record['session_id'],self.cfg,None,self.record['image_id'])
    def test_invalid_timestamp_rejected_before_raw_write(self):
        with self.assertRaises(ValueError):persist_scene(self.root,replace(self.p,timestamp_utc='invalid'),self.roi,'b','s',self.cfg)
        self.assertEqual(len(list((self.root/'raw').rglob('*.png'))),1)
    def test_no_approved_data_is_not_reported_as_perfect(self):
        report=build_audit(self.root)
        self.assertTrue(all(m['accuracy_including_abstention'] is None for m in report['metrics'].values()))
    def test_abstention_is_visible_in_accuracy_and_coverage(self):
        self.approve();report=build_audit(self.root);m=report['metrics']['number']
        self.assertEqual((m['compared'],m['correct'],m['abstained']),(1,0,1))
        self.assertEqual(m['accuracy_including_abstention'],0);self.assertIsNone(m['accuracy_when_predicted']);self.assertEqual(m['prediction_coverage'],0)
        self.assertEqual(report['metrics']['color']['correct'],1);self.assertEqual(len(report['mismatches']),1)
    def test_missing_prediction_count_is_explicit_not_scored_as_correct(self):
        self.approve();(self.root/'predictions'/(self.record['image_id']+'.json')).unlink();report=build_audit(self.root)
        self.assertEqual(report['unmatched_truth_counts'],{'occupancy':1,'number':1,'color':1});self.assertEqual(report['metrics']['number']['compared'],0)
        self.assertTrue(report['errors'])
    def test_current_lot_scope_does_not_include_other_data(self):
        self.approve();report=build_audit(self.root,lot='B')
        self.assertEqual(report['disk_counts']['originals'],0);self.assertEqual(len(report['slot_coverage']),16)
        self.assertEqual(report['lots']['B']['frames'],0)
    def test_current_session_scope_does_not_include_other_data(self):
        self.approve();report=build_audit(self.root,session='other');self.assertEqual(report['disk_counts']['originals'],0)
    def test_partial_roi_is_not_counted_as_full_frame(self):
        self.approve();report=build_audit(self.root)
        self.assertEqual(report['lots']['A']['frames'],1);self.assertEqual(report['lots']['A']['full_roi_frames'],0)
        self.assertEqual(report['lots']['A']['fully_reviewed_frames'],0);self.assertEqual(len(report['slot_coverage']),28)
    def test_malformed_review_keeps_valid_original_count(self):
        atomic_json(self.root/'reviews'/(self.record['image_id']+'.json'),{'broken':True});report=build_audit(self.root)
        self.assertEqual(report['disk_counts']['originals'],1);self.assertTrue(report['errors'])
    def test_export_reports_match_returned_counts(self):
        self.approve();path,report=export_audit(self.root,lot='A');saved=read_json(path)
        self.assertEqual(saved['metrics'],report['metrics']);self.assertTrue(Path(path).with_suffix('.md').is_file())
        self.assertIn('보류 포함 일치율',Path(path).with_suffix('.md').read_text(encoding='utf-8'))
    def test_wrong_prediction_not_hidden_by_conditional_accuracy(self):
        rows=rows_for_edit(self.record);rows[0].update(occupancy='vacant',review_status='approved')
        save_review(self.root,self.record,rows,'tester');m=build_audit(self.root)['metrics']['occupancy']
        self.assertEqual((m['compared'],m['predicted'],m['correct']),(1,1,0));self.assertEqual(m['accuracy_when_predicted'],0)


class DashboardV21(unittest.TestCase):
    def setUp(self):
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.window=tk.Tk();self.window.withdraw()
        self.cfg,self.p,self.roi=fixture();self.app=Dashboard(self.window,self.cfg,Path(self.temp.name),PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop();self.temp.cleanup()
        self.app=None;self.window=None;gc.collect()
    def test_camera_settings_clear_results_before_new_input(self):
        a=self.app;a.hub=MagicMock();a.hub.is_stopped.return_value=True
        a.scene=(self.p,self.roi);a.roi=self.roi;a.pred=VisionEngine().run(self.p,self.roi,self.cfg);a.record={'image_id':'old'}
        with patch('camera_tools.CameraTools'),patch.object(self.window,'wait_window'):a.camera_settings()
        self.assertIsNone(a.pred);self.assertIsNone(a.record);self.assertIsNone(a.scene);self.assertEqual(a.rows,[])
    def test_unsaved_labels_cannot_be_silently_exported(self):
        a=self.app;a.dirty=True
        with self.assertRaises(ValueError):a.export_current('export')
        with self.assertRaises(ValueError):a.export_current('audit')
    def test_audit_scope_token_matches_filter(self):
        a=self.app;a.scope.set('현재 주차장');a.lot.set('B')
        with patch.object(a,'submit') as submit:a.export_current('audit')
        self.assertEqual(submit.call_args.args,('audit',(a.root,'B',None),(str(a.root),'현재 주차장','B',None)))
    def test_stale_audit_is_not_shown_as_current_scope(self):
        a=self.app;report=build_audit(a.root);token=(str(a.root),'전체',None,None);a.scope.set('현재 주차장')
        a.accept('audit',token,('unused.json',report),None);self.assertIsNone(a.audit_result)
    def test_no_data_display_does_not_show_100_percent(self):
        a=self.app;report=build_audit(a.root);token=(str(a.root),'전체',None,None)
        a.accept('audit',token,('unused.json',report),None)
        self.assertIn('평가 자료 없음',a.audit_text.get());self.assertNotIn('100',a.audit_text.get())
    def test_fast_worker_labels_ocr_as_pending(self):
        from dashboard import Worker
        cfg,p,roi,ref=sticker_packet();worker=Worker();worker.start()
        try:
            worker.jobs.put(('analyze',1,(p,roi,{**cfg,'enable_ocr':True},ref,'reference')))
            _,_,pred,error=worker.results.get(timeout=5);self.assertIsNone(error)
            self.assertEqual(pred['spaces'][0]['ocr_status'],'pending');self.assertNotIn('OCR 꺼짐',pred['spaces'][0]['issues'])
        finally:worker.jobs.put(None);worker.join(5)


if __name__=='__main__':unittest.main()
