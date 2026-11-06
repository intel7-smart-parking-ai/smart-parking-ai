"""Real OpenCV marker detection on synthetic boards; no physical accuracy claim."""
import copy,hashlib,json,tempfile,time,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import cv2,numpy as np
from test_v19 import fixture,PROJECT
from layouts import layout,snapshot,validate_snapshot
from capture_views import capture_pose
from marker_setup import enroll,locate,load_template,template_path,fit,detect,validate_template,digest,METHOD
from records import validate_capture,load_original
from dataset19 import persist_scene,DatasetIndex
from collection_output import read_collection,export_collection,labels_for_scene
from pipeline import read_json


def board(lot='A',count=4):
    cfg,p,_=fixture();cfg['capture_view']='ceiling';cfg['capture_source']={'kind':'import'}
    image=np.full((1100,1600,3),245,np.uint8)
    positions=[(40,40),(1480,40),(1480,980),(40,980),(760,40),(760,980)]
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for i,(x,y) in enumerate(positions[:count]):
        tag=cv2.aruco.generateImageMarker(dictionary,i+(10 if lot=='B' else 0),80)
        image[y:y+80,x:x+80]=cv2.cvtColor(tag,cv2.COLOR_GRAY2BGR)
    slots=layout(lot)['spaces'];n=len(slots)//2
    for i,s in enumerate(slots):
        x=200+(i%n)*int(1200/n);y=230+(i//n)*400;w=int(1200/n)-12
        q=[[x,y],[x+w,y],[x+w-25 if lot=='B' else x+w,y+230],[x-25 if lot=='B' else x,y+230]]
        s.update(polygon=q,boundary_kind='open_u',painted_edges=[[q[3],q[0]],[q[0],q[1]],[q[1],q[2]]],entrance_edge=[q[2],q[3]],line_support=[1.,1.,1.])
        cv2.polylines(image,[np.int32(q)[[3,0,1,2]]],False,(40,40,40),3)
    p=replace(p,image=image,source_kind='import')
    roi=snapshot(lot,p.camera_id,p.serial,[1600,1100],capture_pose(cfg),slots)
    return cfg,p,roi


def moved(p):
    src=np.float32([[0,0],[1599,0],[1599,1099],[0,1099]])
    dst=np.float32([[80,80],[1520,30],[1500,1020],[30,1000]])
    h=cv2.getPerspectiveTransform(src,dst)
    return replace(p,image=cv2.warpPerspective(p.image,h,(1600,1100),borderValue=(245,245,245)),sequence=p.sequence+1),h


class Markers(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=board()
    def tearDown(self):self.temp.cleanup()
    def register(self):return enroll(self.root,self.p,self.roi)
    def test_a_twelve_and_b_sixteen_open_u_bays(self):
        for lot,n in [('A',12),('B',16)]:
            cfg,p,roi=board(lot);result=enroll(self.root,p,roi)
            self.assertEqual(len(result['roi']['spaces']),n);validate_capture(p,result['roi'])
            for s in result['roi']['spaces']:self.assertEqual(s['entrance_edge'],s['polygon'][2:])
    def test_motion_updates_coordinates_with_subpixel_ground_truth_error(self):
        self.register();p,h=moved(self.p);result=locate(load_template(self.root,'A'),p,self.roi['pose'])
        from marker_setup import transform
        expected=transform([s['polygon'] for s in self.roi['spaces']],h)
        actual=np.array([s['polygon'] for s in result['roi']['spaces']]).reshape(-1,2)
        self.assertLess(np.linalg.norm(expected-actual,axis=1).max(),1.5)
        self.assertNotEqual(result['roi']['spaces'],self.roi['spaces'])
    def test_rotated_board_preserves_bay_identity(self):
        self.register();p=replace(self.p,image=cv2.rotate(self.p.image,cv2.ROTATE_180),sequence=self.p.sequence+1)
        result=locate(load_template(self.root,'A'),p,self.roi['pose'])
        expected=np.array(self.roi['spaces'][0]['polygon'])*[-1,-1]+[1599,1099]
        self.assertLess(np.linalg.norm(np.array(result['roi']['spaces'][0]['polygon'])-expected,axis=1).max(),1.5)
        self.assertEqual(result['roi']['spaces'][0]['space_id'],'A-01')
    def test_one_corner_hidden_still_tracks(self):
        self.register();image=self.p.image.copy();image[:150,:150]=245
        result=locate(load_template(self.root,'A'),replace(self.p,image=image),self.roi['pose'])
        self.assertEqual(result['marker_count'],3)
    def test_two_visible_markers_hold(self):
        self.register();image=self.p.image.copy();image[:150]=245
        with self.assertRaisesRegex(ValueError,'최소 3개'):locate(load_template(self.root,'A'),replace(self.p,image=image),self.roi['pose'])
    def test_three_markers_cannot_enroll(self):
        image=self.p.image.copy();image[:150,:150]=245
        with self.assertRaisesRegex(ValueError,'최소 4개'):enroll(self.root,replace(self.p,image=image),self.roi)
        self.assertFalse(template_path(self.root,'A').exists())
    def test_wrong_lot_rejected(self):
        with self.assertRaisesRegex(ValueError,'다른 주차장'):detect(self.p.image,'B')
    def test_duplicate_marker_rejected(self):
        image=self.p.image.copy();image[40:120,1480:1560]=image[40:120,40:120]
        with self.assertRaisesRegex(ValueError,'중복'):detect(image,'A')
    def test_marker_outside_declared_ids_rejected(self):
        image=self.p.image.copy();image[40:120,1480:1560]=cv2.cvtColor(cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),40,80),cv2.COLOR_GRAY2BGR)
        with self.assertRaisesRegex(ValueError,'미지정'):detect(image,'A')
    def test_reference_hash_corruption_rejected(self):
        self.register();t=load_template(self.root,'A');t['markers'][0]['corners'][0][0]+=1
        with self.assertRaisesRegex(ValueError,'해시'):validate_template(t)
    def test_changed_marker_position_is_not_silently_discarded(self):
        self.register();t=load_template(self.root,'A');rows=copy.deepcopy(t['markers'])
        rows[0]['corners']=(np.array(rows[0]['corners'])+[20,0]).tolist()
        with self.assertRaisesRegex(ValueError,'불량'):fit(t,rows,[1600,1100])
    def test_unenrolled_id_is_rejected(self):
        self.register();t=load_template(self.root,'A');rows=copy.deepcopy(t['markers']);rows[0]['id']=4
        with self.assertRaisesRegex(ValueError,'기준에 없던'):fit(t,rows,[1600,1100])
    def test_tiny_markers_hold(self):
        self.register();t=load_template(self.root,'A');rows=copy.deepcopy(t['markers'])
        p=np.array(rows[0]['corners']);rows[0]['corners']=(p.mean(axis=0)+(p-p.mean(axis=0))*.1).tolist()
        with self.assertRaisesRegex(ValueError,'20px'):fit(t,rows,[1600,1100])
    def test_offscreen_bay_hold(self):
        self.register();t=load_template(self.root,'A')
        from marker_setup import mapped_spaces
        with self.assertRaisesRegex(ValueError,'전체 주차칸'):mapped_spaces(t,[[1,0,1000],[0,1,0],[0,0,1]],[1600,1100])
    def test_other_camera_or_resolution_hold(self):
        self.register();t=load_template(self.root,'A')
        for p in [replace(self.p,serial='OTHER'),replace(self.p,image=self.p.image[::2,::2])]:
            with self.assertRaisesRegex(ValueError,'카메라/해상도'):locate(t,p,self.roi['pose'])
    def test_changed_view_keeps_template_but_uses_new_pose(self):
        self.register();p,_=moved(self.p);cfg={**self.cfg,'capture_view':'left'}
        result=locate(load_template(self.root,'A'),p,capture_pose(cfg))
        self.assertEqual(result['roi']['pose'],capture_pose(cfg));validate_capture(p,result['roi'])
    def test_frame_id_and_pixels_cannot_reuse_old_evidence(self):
        roi=self.register()['roi']
        for p in [replace(self.p,sequence=self.p.sequence+1),replace(self.p,stream_epoch=self.p.stream_epoch+1),replace(self.p,image=np.zeros_like(self.p.image))]:
            with self.assertRaises(ValueError):validate_capture(p,roi)
    def test_roi_or_matrix_or_stat_tampering_rejected(self):
        roi=self.register()['roi']
        for target in ['polygon','matrix','statistics']:
            bad=copy.deepcopy(roi)
            if target=='polygon':bad['spaces'][0]['polygon'][0][0]+=1
            elif target=='matrix':bad['marker_evidence']['homography'][0][2]+=10
            else:bad['marker_evidence']['statistics']['marker_count']=42
            with self.assertRaises(ValueError):validate_snapshot(bad)
    def test_atomic_active_template_preserved_if_replace_fails(self):
        self.register();path=template_path(self.root,'A');before=path.read_bytes()
        with patch('marker_setup.atomic_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):enroll(self.root,self.p,self.roi)
        self.assertEqual(path.read_bytes(),before);load_template(self.root,'A')
    def test_marker_path_does_not_initialize_ocr(self):
        self.register();p,_=moved(self.p)
        from automatic_setup import prepare_auto_scene
        with patch('automatic_setup.detect_spaces',side_effect=AssertionError('line/OCR called')):
            result=prepare_auto_scene(self.root,p,'A',self.roi['pose'],lambda:(_ for _ in ()).throw(AssertionError('OCR initialized')))
        self.assertEqual(result['method'],METHOD)
    def test_missing_markers_do_not_fall_back_to_stale_anchor(self):
        self.register();p=replace(self.p,image=np.full_like(self.p.image,245))
        from automatic_setup import prepare_auto_scene
        with patch('automatic_setup.try_anchor',side_effect=AssertionError('stale fallback')):
            with self.assertRaisesRegex(ValueError,'최소 3개'):prepare_auto_scene(self.root,p,'A',self.roi['pose'],lambda:None)
    def test_corrupt_template_does_not_fall_back(self):
        self.register();template_path(self.root,'A').write_text('{',encoding='utf-8')
        from automatic_setup import prepare_auto_scene
        with self.assertRaises(ValueError):prepare_auto_scene(self.root,self.p,'A',self.roi['pose'],lambda:None)
    def test_existing_full_anchor_enrolls_automatically_once(self):
        from automatic_setup import prepare_auto_scene
        with patch('automatic_setup.try_anchor',return_value={'ready':True,'roi':self.roi}):
            result=prepare_auto_scene(self.root,self.p,'A',self.roi['pose'],lambda:None)
        self.assertEqual(result['method'],METHOD);before=template_path(self.root,'A').read_bytes()
        prepare_auto_scene(self.root,self.p,'A',self.roi['pose'],lambda:None)
        self.assertEqual(template_path(self.root,'A').read_bytes(),before)
    def test_initial_line_ocr_result_enrolls_automatically(self):
        from automatic_setup import prepare_auto_scene
        evidence=[]
        for s in self.roi['spaces']:
            x,y=s['polygon'][0];evidence.append({'space_id':s['space_id'],'text':s['space_id'],'bbox':[x+20,y+30,x+70,y+60],'score':.99})
        spaces=copy.deepcopy(self.roi['spaces'])
        for s in spaces:
            for k in ['boundary_kind','painted_edges','entrance_edge','line_support']:s.pop(k,None)
        result={'ready':True,'spaces':spaces,'expected_count':12,'detected_count':12,'missing':[],'duplicate_names':[],
                'errors':[],'text_evidence':evidence,'min_score':.7,'method':'auto_line_and_text_v1'}
        with patch('automatic_setup.detect_spaces',return_value=result):
            actual=prepare_auto_scene(self.root,self.p,'A',self.roi['pose'],lambda:None)
        self.assertEqual(actual['method'],METHOD);self.assertFalse(actual['roi']['operator_confirmed'])
    def test_clustered_observations_rejected(self):
        cfg,p,roi=board(count=6);enroll(self.root,p,roi);t=load_template(self.root,'A')
        rows=[r for r in t['markers'] if r['id'] in (0,1,4)]
        with self.assertRaisesRegex(ValueError,'한쪽'):fit(t,rows,[1600,1100])
    def static_text_scene(self):
        from static_markings import appearance
        image=self.p.image.copy();box=[230,280,370,340]
        cv2.putText(image,'A-01',(235,325),cv2.FONT_HERSHEY_SIMPLEX,1.4,(20,20,20),3)
        p=replace(self.p,image=image);roi=copy.deepcopy(self.roi)
        roi['auto_validation']={'text_evidence':[{'space_id':'A-01','bbox':box,'appearance':appearance(image,box)}]}
        enroll(self.root,p,roi);return moved(p)[0]
    def test_printed_bay_text_remains_excluded_after_camera_movement(self):
        from static_markings import unchanged_regions
        p=self.static_text_scene();roi=locate(load_template(self.root,'A'),p,self.roi['pose'])['roi']
        self.assertIn('A-01',unchanged_regions(p.image,roi))
    def test_changed_pixels_are_not_blanket_masked_as_floor_text(self):
        from marker_setup import transform
        from static_markings import unchanged_regions
        p=self.static_text_scene();roi=locate(load_template(self.root,'A'),p,self.roi['pose'])['roi']
        image=p.image.copy();q=transform([[230,280],[370,280],[370,340],[230,340]],roi['marker_evidence']['homography'])
        cv2.fillConvexPoly(image,np.int32(np.rint(q)),(150,150,150))
        p=replace(p,image=image);roi=locate(load_template(self.root,'A'),p,self.roi['pose'])['roi']
        self.assertNotIn('A-01',unchanged_regions(p.image,roi))
    def test_template_markers_must_surround_all_bays(self):
        self.register();t=load_template(self.root,'A')
        for r in t['markers']:r['corners']=(np.array(r['corners'])*.3+400).tolist()
        t['sha256']=digest({k:v for k,v in t.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'둘러싸세요'):validate_template(t)
    def test_marker_recovery_uses_same_pixels_and_evidence(self):
        roi=self.register()['roi']
        with patch('pipeline.atomic_json',side_effect=OSError('manifest write interrupted')):
            with self.assertRaises(OSError):persist_scene(self.root,self.p,roi,'batch','session',self.cfg)
        from capture_recovery import recover_captures
        result=recover_captures(self.root,self.cfg);self.assertEqual(result['복구완료수'],1,result)
        record=read_json(next((self.root/'manifests').glob('*.json')))
        self.assertEqual(record['roi'],roi);self.assertTrue(np.array_equal(load_original(self.root,record),self.p.image))
    def test_sidecar_marker_evidence_must_match_even_when_file_hash_is_updated(self):
        roi=self.register()['roi'];record,_=persist_scene(self.root,self.p,roi,'batch','session',self.cfg)
        bundlepath=self.root/'bundles'/(record['image_id']+'.json');bundle=read_json(bundlepath)
        item=next(i for i in bundle['items'] if i['kind']=='korean_label');path=self.root/item['path'];value=read_json(path)
        value['마커등록근거']['검출마커수']=99;path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();bundlepath.write_text(json.dumps(bundle,ensure_ascii=False),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'마커등록근거'):read_collection(self.root,record)
    def test_marker_images_never_become_automatic_human_approval(self):
        roi=self.register()['roi'];record,_=persist_scene(self.root,self.p,roi,'batch','session',self.cfg)
        counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['quality_held'],1);self.assertEqual(counts['occupancy_approved'],0)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
    def test_selected_and_training_exports_preserve_marker_coordinates(self):
        image=self.p.image.copy();rng=np.random.default_rng(31)
        for s in self.roi['spaces']:
            x,y=s['polygon'][0];image[y+10:y+220,x+10:x+170]=rng.integers(50,210,(210,160,3),dtype=np.uint8)
        p=replace(self.p,image=image);roi=enroll(self.root,p,self.roi)['roi']
        record,_=persist_scene(self.root,p,roi,'batch','session',self.cfg)
        from review import rows_for_edit,save_review
        rows=rows_for_edit(record)
        for r in rows:r.update(occupancy='vacant',review_status='approved')
        save_review(self.root,record,rows,'synthetic-test')
        from dataset19 import export_training
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['quality_passed'],1)
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['space_pairs'],12)
        self.assertEqual(export_training(self.root,selected_only=True)[1],12)
    def test_self_contained_evidence_survives_template_removal_for_saved_data(self):
        roi=self.register()['roi'];record,receipt=persist_scene(self.root,self.p,roi,'same-batch','session',self.cfg)
        self.assertEqual(receipt['status'],'complete');template_path(self.root,'A').unlink()
        validate_snapshot(record['roi']);load_original(self.root,record)
        self.assertEqual(read_collection(self.root,record)['status'],'complete')
        path,report=export_collection(self.root);self.assertEqual(report['counts']['space_pairs'],12)
        labels=labels_for_scene(record);self.assertEqual(labels[0]['마커등록근거']['검출마커수'],4)
    def test_original_and_scene_pairs_follow_moving_roi(self):
        self.register();p,_=moved(self.p);roi=locate(load_template(self.root,'A'),p,self.roi['pose'])['roi']
        record,receipt=persist_scene(self.root,p,roi,'same-batch','session',self.cfg)
        self.assertEqual(receipt['status'],'complete');self.assertTrue(np.array_equal(load_original(self.root,record),p.image))
        labels=labels_for_scene(record);self.assertEqual(labels[0]['주차칸꼭짓점_px'],roi['spaces'][0]['polygon'])
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['collection_pairs'],12)
    def test_worker_updates_token_and_prediction_together(self):
        roi=self.register()['roi'];p,_=moved(self.p)
        from dashboard import Worker
        worker=Worker();worker.start();cfg={**self.cfg,'auto_anchor_root':str(self.root),'enable_ocr':False}
        token=(3,p,roi,None,('batch','session'))
        try:
            worker.jobs.put(('analyze',token,(p,roi,cfg,None,'manual')))
            kind,newtoken,result,error=worker.results.get(timeout=15)
            self.assertIsNone(error);self.assertNotEqual(newtoken[2]['spaces'],roi['spaces'])
            validate_capture(p,newtoken[2]);self.assertEqual(result['frame_id'],p.sequence)
            from vision19 import context_key
            self.assertEqual(result['context_key'],context_key(newtoken[2]))
        finally:worker.jobs.put(None);worker.join(10)
    def test_ui_publishes_and_saves_transformed_frame(self):
        import tkinter as tk
        from dashboard import Dashboard
        roi=self.register()['roi'];p,_=moved(self.p);roi=locate(load_template(self.root,'A'),p,self.roi['pose'])['roi']
        window=tk.Tk();window.withdraw();app=Dashboard(window,self.cfg,self.root,PROJECT)
        try:
            app.input_mode='image';app.roi=roi;app.confirmed_epoch=p.stream_epoch;app.raw=p;app.auto.set(False)
            from vision19 import VisionEngine
            result=VisionEngine().run(p,roi,self.cfg,None,'manual')
            token=(app.generation,p,roi,None,('batch','session'));app.accept('analyze',token,result,None)
            self.assertIs(app.scene[0],p);self.assertEqual(app.scene[1],roi);app.save_scene()
            end=time.monotonic()+15
            while app.saving and time.monotonic()<end:window.update();time.sleep(.01)
            self.assertFalse(app.saving);self.assertIsNotNone(app.record);self.assertEqual(app.record['roi'],roi)
        finally:
            app.dirty=False;app.close();window.mainloop();app=None;window=None
            import gc
            gc.collect()
    def test_marker_loss_discards_late_identity_result(self):
        import tkinter as tk,gc
        from dashboard import Dashboard
        roi=self.register()['roi'];window=tk.Tk();window.withdraw();app=Dashboard(window,self.cfg,self.root,PROJECT)
        try:
            app.auto_run=True;app.input_mode='camera';app.live=True;app.roi=roi;app.confirmed_epoch=self.p.stream_epoch
            token=(app.generation,self.p,roi,None,('batch','session'))
            app.accept('analyze',token,None,'마커 부족')
            self.assertFalse(app.live);self.assertEqual(app.auto_phase,'waiting')
            app.accept('identify',token,{'old_result':True},None)
            self.assertIsNone(app.scene);self.assertIsNone(app.pred);self.assertFalse(app.saving)
        finally:app.dirty=False;app.close();window.mainloop();app=None;window=None;gc.collect()
