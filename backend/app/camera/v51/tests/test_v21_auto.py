"""Automatic registration, by-bay Korean assets, export and UI integration."""
import copy,gc,hashlib,json,tempfile,time,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch,MagicMock
import tkinter as tk
import cv2,numpy as np
from test_v19 import fixture,PROJECT
from test_v20 import sticker_packet
from test_v21 import Reader
from automatic_setup import detect_spaces,normalize_bay,prepare_auto_scene,try_anchor,anchor_alignment
from layouts import layout,validate_snapshot
from vision19 import VisionEngine
from dataset19 import persist_scene,DatasetIndex
from collection_output import read_collection,export_collection,labels_for_scene
from review import rows_for_edit,save_review
from pipeline import read_json
from parking_ai.storage import atomic_json


def board(lot='A'):
    columns=6 if lot=='A' else 8;image=np.full((540,columns*200,3),65,np.uint8);observations=[]
    for i,slot in enumerate(layout(lot)['spaces']):
        x=i%columns*200+8;y=i//columns*260+8
        poly=[[x+20,y],[x+180,y],[x+160,y+240],[x,y+240]] if lot=='B' else [[x,y],[x+180,y],[x+180,y+240],[x,y+240]]
        cv2.polylines(image,[np.asarray(poly)],True,(235,235,235),3)
        text=slot['space_id'];size,bl=cv2.getTextSize(text,cv2.FONT_HERSHEY_SIMPLEX,.65,2)
        tx=x+40;ty=y+212;cv2.putText(image,text,(tx,ty),cv2.FONT_HERSHEY_SIMPLEX,.65,(235,235,235),2)
        observations.append(([[tx,ty-size[1]],[tx+size[0],ty-size[1]],[tx+size[0],ty+bl],[tx,ty+bl]],text,.99))
    class BayReader:
        def __init__(self):self.observations=observations
        def readtext(self,pixels,**kwargs):
            scale=pixels.shape[1]/image.shape[1]
            return [(np.asarray(points)*scale,text,score) for points,text,score in self.observations]
    cfg,p,_=fixture(lot);p=replace(p,image=image,source_kind='synthetic')
    return cfg,p,BayReader()


class AutomaticGeometry(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def setup(self,lot='A'):
        cfg,p,reader=board(lot);result=prepare_auto_scene(self.root,p,lot,'test-pose',lambda:reader)
        return cfg,p,reader,result
    def test_rectangle_all_twelve(self):
        _,p,r,result=self.setup();self.assertTrue(result['ready']);self.assertEqual(len(result['roi']['spaces']),12)
    def test_diagonal_all_sixteen(self):
        _,p,r,result=self.setup('B');self.assertTrue(result['ready']);self.assertEqual(len(result['roi']['spaces']),16)
        self.assertTrue(any(s['polygon'][0][0]!=s['polygon'][-1][0] for s in result['roi']['spaces']))
    def test_registration_never_claims_human_confirmation(self):
        *_,r=self.setup();self.assertIs(r['roi']['operator_confirmed'],False);validate_snapshot(r['roi'])
    def test_missing_name_does_not_invent_expected_id(self):
        _,p,r=board();r.observations.pop();result=detect_spaces(p.image,'A',r)
        self.assertFalse(result['ready']);self.assertIn('B-06',result['missing'])
    def test_duplicate_name_in_different_locations_holds_registration(self):
        _,p,r=board();pts,_,score=r.observations[1];r.observations[1]=(pts,'A-01',score)
        result=detect_spaces(p.image,'A',r);self.assertFalse(result['ready']);self.assertIn('A-01',result['duplicate_names'])
    def test_vehicle_number_and_unknown_bay_not_used_as_names(self):
        for value in ['23','A-09','B-99','A-01x','가01']:self.assertIsNone(normalize_bay(value,'A'))
        self.assertEqual(normalize_bay(' b 6 ','A'),'B-06')
    def test_plain_labels_without_lines_do_not_register(self):
        _,p,r=board();result=detect_spaces(np.full_like(p.image,65),'A',r);self.assertFalse(result['ready'])
    def test_same_anchor_reused_without_ocr(self):
        _,p,_,r=self.setup();factory=MagicMock(side_effect=AssertionError('must reuse'))
        result=prepare_auto_scene(self.root,replace(p,sequence=2), 'A','test-pose',factory)
        self.assertTrue(result['ready']);self.assertEqual(result['method'],'verified_anchor_reuse');factory.assert_not_called()
    def test_moved_view_is_not_reused(self):
        _,p,_,r=self.setup();moved=cv2.warpAffine(p.image,np.float32([[1,0,18],[0,1,0]]),(p.image.shape[1],p.image.shape[0]))
        self.assertIsNone(try_anchor(self.root,replace(p,image=moved),'A','test-pose'))
    def test_camera_or_pose_change_invalidates_anchor(self):
        _,p,_,_=self.setup();self.assertIsNone(try_anchor(self.root,replace(p,serial='another'),'A','test-pose'))
        self.assertIsNone(try_anchor(self.root,p,'A','different-pose'))
    def test_corrupt_anchor_not_reused(self):
        _,p,_,_=self.setup();path=next((self.root/'calibration_anchors').glob('*.png'));path.write_bytes(b'bad')
        self.assertIsNone(try_anchor(self.root,p,'A','test-pose'))
    def test_missing_text_evidence_rejected(self):
        *_,r=self.setup();r['roi']['auto_validation']['text_evidence'].pop()
        with self.assertRaises(ValueError):validate_snapshot(r['roi'])
    def test_neighbor_name_evidence_rejected(self):
        *_,r=self.setup();rows=r['roi']['auto_validation']['text_evidence'];rows[0]['bbox']=rows[1]['bbox']
        with self.assertRaises(ValueError):validate_snapshot(r['roi'])
    def test_repeated_failed_registration_keeps_bounded_diagnostics(self):
        _,p,r=board();r.observations.pop()
        for i in range(2):prepare_auto_scene(self.root,p,'A','test-pose',lambda:r)
        self.assertEqual(len(list((self.root/'setup_attempts').glob('*.json'))),1)


class KoreanCollection(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi,self.ref=sticker_packet();engine=VisionEngine();engine.reader=Reader()
        self.pred=engine.enrich(self.p,self.roi,self.cfg,engine.run(self.p,self.roi,self.cfg,self.ref))
        self.record,self.receipt=persist_scene(self.root,self.p,self.roi,'batch','session',self.cfg,self.pred)
        self.bundle=read_collection(self.root,self.record)
    def tearDown(self):self.temp.cleanup()
    def item(self,kind,sid=None):return next(x for x in self.bundle['items'] if x['kind']==kind and (sid is None or x['space_id']==sid))
    def test_bay_folder_and_matching_filename_json(self):
        sid=self.roi['spaces'][0]['space_id'];im=Path(self.item('annotated_space',sid)['path']);js=Path(self.item('korean_label',sid)['path'])
        self.assertEqual(im.stem,js.stem);self.assertEqual(im.parent,Path('collected/A')/sid);self.assertTrue(im.name.startswith(sid+'__'))
    def test_korean_values_numeric_id_coordinates_and_provenance(self):
        data=read_json(self.root/self.item('korean_label',self.roi['spaces'][0]['space_id'])['path'])
        self.assertEqual(data['차량번호'],23);self.assertEqual(data['스티커색'],'초록');self.assertIs(data['사람검수완료'],False)
        self.assertEqual(data['주차칸꼭짓점_px'],self.roi['spaces'][0]['polygon']);self.assertIsInstance(data['번호판독점수'],float)
        self.assertIsInstance(data['영상품질']['선명도_라플라시안분산'],float);self.assertIn('자동',data['라벨출처'])
    def test_raw_pixels_unchanged_annotation_larger_than_crop(self):
        from records import load_original
        np.testing.assert_array_equal(load_original(self.root,self.record),self.p.image)
        from PIL import Image
        with Image.open(self.root/self.item('annotated_space')['path']) as im:self.assertGreater(im.height,241)
    def test_counts_report_completed_pairs(self):
        counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual((counts['originals'],counts['annotated_spaces'],counts['korean_labels'],counts['collection_pairs']),(1,2,2,2))
        self.assertEqual((counts['annotated_scenes'],counts['scene_labels'],counts['partial']),(1,1,0))
        self.assertEqual((counts['information_complete'],counts['needs_attention']),(2,0))
    def test_missing_json_reduces_pairs_and_blocks_scene_export(self):
        (self.root/self.item('korean_label')['path']).unlink();counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['collection_pairs'],1);self.assertEqual(counts['originals'],1);self.assertEqual(counts['partial'],1)
        path,result=export_collection(self.root);self.assertEqual(result['counts']['scenes'],0);self.assertEqual(len(result['skipped']),1)
    def test_export_reports_actual_counts_and_preserves_sources(self):
        original=self.root/self.record['raw_path'];before=original.read_bytes();path,result=export_collection(self.root)
        self.assertEqual(result['counts'],{'scenes':1,'space_pairs':2,'images':4,'json_files':3,'originals':1})
        self.assertEqual(len(list(Path(path).rglob('*.png'))),4)
        # Three data sidecars, one export receipt, and the V25 completion marker.
        self.assertEqual(len(list(Path(path).rglob('*.json'))),5)
        self.assertEqual(read_json(Path(path)/'EXPORT_STATUS.json')['상태'],'완료')
        self.assertEqual(before,original.read_bytes());self.assertEqual(before,(Path(path)/self.record['raw_path']).read_bytes())
    def test_export_scope(self):
        for kwargs in [{'lot':'B'},{'session':'other'}]:self.assertEqual(export_collection(self.root,**kwargs)[1]['counts']['scenes'],0)
    def test_corrupt_annotated_image_excluded(self):
        (self.root/self.item('annotated_space')['path']).write_bytes(b'wrong');result=export_collection(self.root)[1]
        self.assertEqual(result['counts']['images'],0);self.assertEqual(len(result['skipped']),1)
    def test_export_write_failure_is_failed_not_success(self):
        with patch('collection_output.atomic_bytes',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):export_collection(self.root)
        self.assertEqual(len(list((self.root/'exports').rglob('FAILED.json'))),1)
    def test_review_refreshes_photo_and_sidecar_without_autoapproval(self):
        rows=rows_for_edit(self.record);rows[0].update(occupancy='occupied',plate_text='23',color='green',
            plate_bbox=self.pred['spaces'][0]['plate_bbox'],sticker_polygon=self.pred['spaces'][0]['sticker_polygon'],review_status='approved')
        old=self.item('annotated_space')['sha256'];save_review(self.root,self.record,rows,'tester');self.bundle=read_collection(self.root,self.record)
        data=read_json(self.root/self.item('korean_label',rows[0]['space_id'])['path'])
        self.assertTrue(data['사람검수완료']);self.assertNotEqual(old,self.item('annotated_space')['sha256'])
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['collection_pairs'],2)
    def test_partial_bundle_cannot_be_exported(self):
        self.bundle.update(status='partial',errors=[{'error':'interrupted'}]);atomic_json(self.root/'bundles'/(self.record['image_id']+'.json'),self.bundle)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_empty_sticker_view_is_unknown_not_vacant(self):
        cfg,p,roi=fixture();pred=VisionEngine().run(p,roi,cfg,mode='sticker')
        self.assertTrue(all(r['occupancy']=='unknown' for r in pred['spaces']))
    def test_sticker_presence_is_candidate_without_fake_confidence(self):
        pred=VisionEngine().run(self.p,self.roi,self.cfg,mode='sticker');row=pred['spaces'][0]
        self.assertEqual(row['occupancy'],'occupied');self.assertEqual(row['score_kind'],'sticker_presence');self.assertIsNone(row['occupancy_score'])


class AutomaticUI(unittest.TestCase):
    def setUp(self):
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.window=tk.Tk();self.window.withdraw()
        self.cfg,self.p,self.reader=board();self.app=Dashboard(self.window,self.cfg,self.root,PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop();self.temp.cleanup()
        self.app=None;self.window=None;gc.collect()
    def ready(self):return prepare_auto_scene(self.root,self.p,'A',self.cfg['camera_pose']['A'],lambda:self.reader)
    def test_one_start_enables_registration_ocr_and_autosave(self):
        a=self.app;a.hub=MagicMock();a.hub.is_stopped.return_value=True;a.start_automatic()
        self.assertTrue(a.auto_run and a.auto.get() and a.ocr.get());self.assertEqual(a.auto_phase,'waiting')
        a.auto_next_setup=0
        with patch.object(a,'submit') as submit:a.automatic_step(self.p)
        self.assertEqual(submit.call_args.args[0],'calibrate')
    def test_calibration_starts_live_without_manual_confirmation(self):
        a=self.app;a.auto_run=True;a.auto.set(True);a.ocr.set(True);a.accept('calibrate',(a.generation,self.p,'A'),self.ready(),None)
        self.assertTrue(a.live);self.assertEqual(a.auto_phase,'collecting');a.check_ready(self.p);self.assertFalse(a.roi['operator_confirmed'])
    def test_stale_calibration_cannot_restore_changed_context(self):
        a=self.app;a.auto_run=True;a.accept('calibrate',(a.generation-1,self.p,'A'),self.ready(),None);self.assertIsNone(a.roi)
    def test_incomplete_calibration_retries_without_collection(self):
        a=self.app;a.auto_run=True;a.accept('calibrate',(a.generation,self.p,'A'),{'ready':False,'detected_count':1,'expected_count':12,'missing':['B-06']},None)
        self.assertFalse(a.live);self.assertEqual(a.auto_phase,'waiting');self.assertGreater(a.auto_next_setup,time.monotonic())
    def test_failed_save_stops_automatic_loop(self):
        a=self.app;a.auto_run=True;a.auto.set(True);a.live=True;a.accept('save',None,None,'disk full')
        self.assertFalse(a.auto_run or a.live);self.assertIn('저장 실패',a.auto_status.get())
    def test_layout_failure_invalidates_pending_identity(self):
        a=self.app;a.auto_run=True;a.live=True;generation=a.generation;a.accept('analyze',(generation,self.p,None),None,'구도 이동')
        self.assertGreater(a.generation,generation);self.assertFalse(a.live);self.assertEqual(a.auto_phase,'waiting')
    def test_export_result_shows_actual_pair_and_image_counts(self):
        a=self.app;report={'counts':{'scenes':1,'space_pairs':12,'images':14,'originals':1,'json_files':13},'skipped':[]}
        a.accept('collection_export',None,('test-path',report),None)
        self.assertIn('짝 12',a.export_status.get());self.assertIn('이미지 14',a.export_status.get());self.assertIn('JSON 13',a.export_status.get())
    def test_worker_checks_current_view_before_analyzing(self):
        from dashboard import Worker
        result=self.ready();worker=Worker();worker.start()
        shifted=cv2.warpAffine(self.p.image,np.float32([[1,0,20],[0,1,0]]),(self.p.image.shape[1],self.p.image.shape[0]))
        try:
            worker.jobs.put(('analyze',1,(replace(self.p,image=shifted),result['roi'],{**self.cfg,'auto_anchor_root':str(self.root)},None,'sticker')))
            _,_,pred,error=worker.results.get(timeout=10);self.assertIsNone(pred);self.assertIn('구도',error)
        finally:worker.jobs.put(None);worker.join(10)
    def test_one_start_runs_registration_analysis_save_and_export(self):
        a=self.app;a.cfg['warmup_seconds']=0;a.hub=MagicMock();a.hub.is_stopped.return_value=True
        a.hub.get_latest_frame.return_value=self.p;a.hub.statuses.return_value={'A':{'status':'synthetic test'}}
        bay_reader=self.reader
        class CombinedReader:
            def readtext(inner,pixels,**kwargs):
                return bay_reader.readtext(pixels,**kwargs) if pixels.shape[1]>=self.p.image.shape[1] else []
        a.identity_worker.engine.reader=CombinedReader();a.start_automatic()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            self.window.update()
            if a.scan_result and a.scan_result['counts']['collection_pairs']==12:break
            time.sleep(.02)
        self.assertEqual(a.scan_result['counts']['collection_pairs'],12,a.notice.get())
        self.assertEqual(a.scan_result['counts']['label_files'],0);self.assertIsNotNone(a.record)
        a.stop_live();target,result=export_collection(self.root)
        self.assertEqual(result['counts'],{'scenes':1,'space_pairs':12,'images':14,'json_files':13,'originals':1})


if __name__=='__main__':unittest.main()
