"""V24 reproductions: source switches, partial evidence, ROI input and OCR rescue."""
import copy,gc,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from dataclasses import replace
import tkinter as tk
from test_v19 import PROJECT,fixture
from test_v20 import sticker_packet
from test_v21 import Reader
from test_v23_open_u import open_board
from dashboard import Dashboard
from camera_hub import CameraSpec
from vision19 import VisionEngine,Classifier
from dataset19 import persist_scene,DatasetIndex,rebuild_scene_assets
from collection_output import export_collection,read_collection,input_fingerprints
from parking_ai.storage import atomic_json
from review import rows_for_edit,save_review
from pipeline import read_json
from automatic_setup import prepare_auto_scene,try_anchor
from layouts import validate_snapshot


class EvidenceIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.packet,self.roi,self.reference=sticker_packet()
        engine=VisionEngine();engine.reader=Reader();self.pred=engine.enrich(self.packet,self.roi,self.cfg,engine.run(self.packet,self.roi,self.cfg,self.reference))
        self.record,_=persist_scene(self.root,self.packet,self.roi,'batch','session',self.cfg,self.pred)
        self.pp=self.root/'predictions'/(self.record['image_id']+'.json');self.mp=self.root/'manifests'/(self.record['image_id']+'.json')
        self.bp=self.root/'bundles'/(self.record['image_id']+'.json')
    def tearDown(self):self.temp.cleanup()
    def approve_empty(self):
        rows=rows_for_edit(self.record);rows[1].update(occupancy='vacant',review_status='approved')
        return save_review(self.root,self.record,rows,'tester')
    def test_pending_missing_prediction_cannot_be_resaved_as_complete(self):
        self.pp.unlink();record=read_json(self.mp);record['prediction_status']='pending';atomic_json(self.mp,record)
        before=self.mp.read_bytes()
        with self.assertRaises(ValueError):persist_scene(self.root,self.packet,self.roi,'batch','session',self.cfg,None,self.record['image_id'])
        self.assertEqual(self.mp.read_bytes(),before)
    def test_bad_prediction_review_preserves_truth_but_not_complete_bundle(self):
        atomic_json(self.pp,{})
        self.approve_empty()
        self.assertEqual(read_json(self.root/'reviews'/self.pp.name)['spaces'][1]['review_status'],'approved')
        self.assertEqual(read_json(self.mp)['collection_status'],'partial')
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_missing_promised_prediction_review_cannot_export(self):
        self.pp.unlink();self.approve_empty()
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['originals'],1)
    def test_legacy_false_complete_bundle_with_matching_bad_fingerprint_is_rejected(self):
        atomic_json(self.pp,{})
        bundle=read_json(self.bp);bundle['input_fingerprints']=input_fingerprints(self.root,self.record['image_id']);atomic_json(self.bp,bundle)
        with self.assertRaises(ValueError):read_collection(self.root,self.record)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_pending_prediction_status_blocks_export_even_when_file_present(self):
        record=read_json(self.mp);record['prediction_status']='pending';atomic_json(self.mp,record)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_repair_with_restored_prediction_uses_saved_review(self):
        original=self.pp.read_bytes();self.pp.unlink();self.approve_empty();self.pp.write_bytes(original)
        record,receipt=rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
        self.assertEqual(receipt['status'],'complete');self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],1)
        self.assertEqual(record['raw_sha256'],self.record['raw_sha256'])


class InputLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.packet,self.roi,_=sticker_packet()
        self.window=tk.Tk();self.window.withdraw();self.app=Dashboard(self.window,self.cfg,self.root,PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop()
        self.app=None;self.window=None;gc.collect();self.temp.cleanup()
    def setup_photo(self):
        a=self.app;a.input_mode='image';a.scene=(self.packet,self.roi);a.raw=self.packet;a.roi=self.roi;a.confirmed_epoch=self.packet.stream_epoch
        a.cfg['capture_source']={'kind':'import','original_filename':'old-photo.jpg'}
    def test_first_camera_connect_clears_photo_and_inference(self):
        self.setup_photo();a=self.app;generation=a.generation;hub=MagicMock();hub.is_stopped.return_value=True
        with patch('dashboard.load_camera_specs',return_value=[CameraSpec('A','file',file_path='mock')]),patch('dashboard.CameraHub',return_value=hub):a.connect()
        self.assertIsNone(a.scene);self.assertIsNone(a.raw);self.assertIsNone(a.roi);self.assertIsNone(a.confirmed_epoch)
        self.assertGreater(a.generation,generation);self.assertEqual(a.input_mode,'camera')
        hub.start.assert_called_once()
    def test_first_camera_connect_respects_unsaved_review_cancel(self):
        self.setup_photo();a=self.app;a.dirty=True
        with patch('dashboard.messagebox.askyesno',return_value=False),patch('dashboard.CameraHub') as factory:a.connect()
        factory.assert_not_called();self.assertEqual(a.input_mode,'image');self.assertIsNotNone(a.scene)
    def test_analysis_during_calibration_is_refused(self):
        from demo import build_demo_scene
        self.setup_photo();a=self.app;packet,roi,_,_=build_demo_scene('A',self.cfg)
        a.scene=(packet,roi);a.roi=roi;a.confirmed_epoch=packet.stream_epoch;a.calibrating=True
        with patch.object(a,'submit') as submit:
            with self.assertRaises(ValueError):a.analyze_once()
        submit.assert_not_called()
    def test_photo_setup_result_waits_for_no_competing_analysis(self):
        # Pressing 1회 분석 during automatic registration must not consume its
        # future one-shot job or silently leave the photo unsaved.
        a=self.app;a.calibrating=True
        with patch.object(a,'get_packet') as packet:
            with self.assertRaises(ValueError):a.analyze_once()
        packet.assert_not_called()


class RoiContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.packet,reader=open_board()
        self.roi=prepare_auto_scene(self.root,self.packet,'A','test-pose',lambda:reader)['roi']
    def tearDown(self):self.temp.cleanup()
    def test_fractional_name_box_rejected_before_pixel_slicing(self):
        self.roi['auto_validation']['text_evidence'][0]['bbox'][0]+=.25
        with self.assertRaises(ValueError):validate_snapshot(self.roi)
    def test_manual_open_boundary_metadata_is_also_validated(self):
        self.roi['operator_confirmed']=True;self.roi.pop('registration_method');self.roi['spaces'][0]['entrance_edge'][0][0]+=10
        with self.assertRaises(ValueError):validate_snapshot(self.roi)


class IdentityRescue(unittest.TestCase):
    def test_local_ocr_sticker_updates_sticker_mode_occupancy(self):
        cfg,p,roi,_=sticker_packet();engine=VisionEngine();engine.reader=Reader()
        normal=engine.run(p,roi,cfg,mode='sticker');polygon=normal['spaces'][0]['sticker_polygon']
        from sticker_ocr import read_sticker
        from contracts import number_config_for_lot
        evidence=read_sticker(p.image,polygon,Reader(),number_config_for_lot('A',cfg))
        with patch('vision19.independent_sticker',return_value=(None,'unknown')):pred=engine.run(p,roi,cfg,mode='sticker')
        proposal={'number_candidate':'23','candidates':[evidence['selected']]}
        with patch('vision19.analyze',return_value=[proposal]),patch('vision19.sticker_region',side_effect=[(polygon,'green')]+[(None,'unknown')]*(len(roi['spaces'])-1)):
            result=engine.enrich(p,roi,cfg,pred)
        row=result['spaces'][0];self.assertEqual(row['number'],'23');self.assertEqual(row['occupancy'],'occupied')
        self.assertEqual(row['score_kind'],'sticker_presence');self.assertIsNone(row['occupancy_score'])


class ModelConfiguration(unittest.TestCase):
    def test_invalid_threshold_rejected_before_openvino_or_file_access(self):
        settings=read_json(PROJECT/'occupancy_model.example.json')
        import numpy as np
        for threshold in (-.01,1.01,float('nan'),float('inf'),True):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError,'threshold'):Classifier().infer(np.zeros((10,10,3),np.uint8),{**settings,'threshold':threshold})
    def test_configuration_validation_checks_model_preprocessing(self):
        from settings import validate_config
        cfg=read_json(PROJECT/'config.json');settings=read_json(PROJECT/'occupancy_model.example.json')
        for key,value in [('std',[1,0,1]),('mean',[0,0]),('scale',float('nan')),('size',[224,0]),('output','unknown'),('labels',['vacant','vacant'])]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):validate_config({**cfg,'occupancy_model':{**settings,key:value}},PROJECT)


class ColorEquivalence(unittest.TestCase):
    def test_sticker_crop_matches_full_frame_sampling_including_borders(self):
        import cv2,numpy as np
        from analysis import color_candidate
        rng=np.random.default_rng(24);image=rng.integers(0,256,(240,320,3),dtype=np.uint8)
        polygons=[[[0,0],[100,0],[100,100],[0,100]],[[20,30],[130,40],[120,180],[40,160]],[[210,140],[319,140],[319,239],[210,239]]]
        for poly in polygons:
            with self.subTest(poly=poly):
                points=np.asarray(poly,np.int32);mask=np.zeros(image.shape[:2],np.uint8);cv2.fillConvexPoly(mask,points,255)
                mask=cv2.erode(mask,np.ones((3,3),np.uint8));h,s,v=cv2.split(cv2.cvtColor(image,cv2.COLOR_BGR2HSV));usable=(mask>0)&(v>=55)
                measured=color_candidate(image,poly)
                self.assertEqual(measured['sample_pixels'],int(usable.sum()))
                expected={'white':(s<55)&(v>=160),'green':(h>=40)&(h<90)&(s>=70)&(v>=60),'blue':(h>=90)&(h<=135)&(s>=70)&(v>=60)}
                for name,pixels in expected.items():self.assertEqual(measured['ratios'][name],float((pixels&usable).sum()/usable.sum()))


if __name__=='__main__':unittest.main()
