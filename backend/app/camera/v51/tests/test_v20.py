"""Regressions for V19 audit findings. Synthetic data, no hardware or live HTTP."""
import copy
from dataclasses import replace
from datetime import datetime,timezone
import gc
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import types
import unittest
from unittest.mock import patch,MagicMock
import urllib.error
import cv2
import numpy as np
from test_v19 import fixture,PROJECT
from dataset19 import persist_scene,DatasetIndex,export_training,validate_prediction
from pipeline import read_json
from review import rows_for_edit,save_review,export_reviewed
from parking_ai.storage import atomic_json
from vision19 import VisionEngine,make_reference,independent_sticker,context_key,Classifier
from transport19 import EventSender,build_events


def sticker_packet():
    cfg,p,roi=fixture();image=p.image.copy()
    image[45:220,35:135]=45
    cv2.rectangle(image,(48,75),(120,128),(0,200,0),-1)
    cv2.putText(image,'23',(56,112),cv2.FONT_HERSHEY_SIMPLEX,.85,(0,0,0),2)
    return cfg,replace(p,image=image),roi,make_reference(p,roi)


class ColorAndWorkers(unittest.TestCase):
    def test_color_available_when_ocr_disabled(self):
        cfg,p,r,ref=sticker_packet();row=VisionEngine().run(p,r,cfg,ref)['spaces'][0]
        self.assertEqual((row['occupancy'],row['color'],row['number']),('occupied','green',None))
        self.assertIsNotNone(row['sticker_polygon'])

    def test_color_survives_ocr_failure_and_input_stays_immutable(self):
        cfg,p,r,ref=sticker_packet();engine=VisionEngine();fast=engine.run(p,r,cfg,ref);before=copy.deepcopy(fast)
        engine.reader=object()
        with patch('vision19.analyze',side_effect=RuntimeError('synthetic OCR failure')):full=engine.enrich(p,r,cfg,fast)
        self.assertEqual(fast,before);self.assertEqual(full['spaces'][0]['color'],'green')
        self.assertIsNone(full['spaces'][0]['number']);self.assertTrue(full['errors'])

    def test_uniform_green_patch_does_not_become_sticker(self):
        _,p,r=fixture();p.image[75:129,48:121]=(0,200,0)
        self.assertEqual(independent_sticker(p.image,r['spaces'][0]['polygon']),(None,'unknown'))

    def test_white_sticker_inside_dark_car_on_bright_floor_is_found(self):
        cfg,p,r,ref=sticker_packet();cv2.rectangle(p.image,(48,75),(120,128),(255,255,255),-1)
        cv2.putText(p.image,'23',(56,112),cv2.FONT_HERSHEY_SIMPLEX,.85,(0,0,0),2)
        row=VisionEngine().run(p,r,cfg,ref)['spaces'][0]
        self.assertEqual(row['color'],'white');self.assertIsNotNone(row['sticker_polygon'])

    def test_blue_sticker_candidate(self):
        cfg,p,r,ref=sticker_packet();cv2.rectangle(p.image,(48,75),(120,128),(200,0,0),-1)
        cv2.putText(p.image,'14',(56,112),cv2.FONT_HERSHEY_SIMPLEX,.85,(0,0,0),2)
        self.assertEqual(VisionEngine().run(p,r,cfg,ref)['spaces'][0]['color'],'blue')

    def test_multiple_plausible_stickers_remain_unknown(self):
        _,p,r,_=sticker_packet()
        cv2.rectangle(p.image,(48,150),(120,203),(200,0,0),-1)
        cv2.putText(p.image,'14',(56,188),cv2.FONT_HERSHEY_SIMPLEX,.85,(0,0,0),2)
        self.assertEqual(independent_sticker(p.image,r['spaces'][0]['polygon']),(None,'unknown'))

    def test_slow_identity_does_not_block_next_occupancy(self):
        from dashboard import Worker
        cfg,p,r,ref=sticker_packet();cfg['enable_ocr']=True
        fast=Worker();identity=Worker();started=threading.Event();release=threading.Event()
        def slow(*args):
            started.set()
            if not release.wait(5):raise TimeoutError('test release missing')
            return args[-1]
        identity.engine.enrich=slow;fast.start();identity.start()
        try:
            initial=VisionEngine().run(p,r,{**cfg,'enable_ocr':False},ref)
            identity.jobs.put(('identify',1,(p,r,cfg,initial)));self.assertTrue(started.wait(3))
            fast.jobs.put(('analyze',2,(replace(p,sequence=2),r,cfg,ref,'reference')))
            result=fast.results.get(timeout=3)
            self.assertIsNone(result[3]);self.assertEqual(result[2]['frame_id'],2)
            self.assertEqual(result[2]['spaces'][0]['occupancy'],'occupied');self.assertFalse(release.is_set())
        finally:
            release.set();fast.jobs.put(None);identity.jobs.put(None);fast.join(5);identity.join(5)
        self.assertFalse(fast.is_alive());self.assertFalse(identity.is_alive())

    def test_model_file_replacement_reloads_compiled_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.onnx';path.write_bytes(b'first')
            class Compiled:
                def output(self,n):return 'result'
                def __call__(self,data):return {'result':np.array([[.1,.9]])}
            core=MagicMock();core.return_value.compile_model.return_value=Compiled()
            settings={'model':str(path),'labels':['vacant','occupied'],'layout':'NCHW','color':'BGR',
                      'size':[32,32],'scale':1,'mean':[0,0,0],'std':[1,1,1],'output':'probabilities'}
            adapter=Classifier();image=np.zeros((20,20,3),np.uint8)
            with patch.dict('sys.modules',{'openvino':types.SimpleNamespace(Core=core)}):
                self.assertEqual(adapter.infer(image,settings)[0],'occupied');adapter.infer(image,settings)
                self.assertEqual(core.return_value.compile_model.call_count,1)
                path.write_bytes(b'replacement with different size');adapter.infer(image,settings)
                self.assertEqual(core.return_value.compile_model.call_count,2)


class DataIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi=fixture();self.pred=VisionEngine().run(self.p,self.roi,self.cfg,make_reference(self.p,self.roi))
    def tearDown(self):self.temp.cleanup()
    def save(self):return persist_scene(self.root,self.p,self.roi,'batch-a','session-a',self.cfg,self.pred)

    def test_missing_assets_is_error_and_original_remains_counted(self):
        record,_=self.save();(self.root/'assets'/(record['image_id']+'.json')).unlink()
        report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['partial'],1)
        self.assertTrue(any('assets' in str(e) for e in report['errors']))

    def test_interrupted_initial_asset_write_is_not_legacy(self):
        from pipeline import save_capture
        save_capture(self.root,self.p,self.roi,'batch','session',self.cfg,scene_assets=True)
        report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['partial'],1)
        self.assertNotEqual(report['entries'][0]['state'],'legacy')

    def test_corrupt_asset_receipt_keeps_raw_count(self):
        record,_=self.save();(self.root/'assets'/(record['image_id']+'.json')).write_text('{broken',encoding='utf-8')
        report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['spaces'],0);self.assertTrue(report['errors'])

    def test_duplicate_asset_is_not_counted_twice(self):
        record,receipt=self.save();receipt['items'].append(copy.deepcopy(receipt['items'][0]))
        atomic_json(self.root/'assets'/(record['image_id']+'.json'),receipt)
        report=DatasetIndex().scan(self.root);self.assertEqual(report['counts']['spaces'],0);self.assertTrue(report['errors'])

    def test_missing_prediction_invalidates_cached_scan(self):
        record,_=self.save();index=DatasetIndex();self.assertFalse(index.scan(self.root)['errors'])
        (self.root/'predictions'/(record['image_id']+'.json')).unlink();report=index.scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['partial'],1)
        self.assertTrue(any('예측' in str(e) for e in report['errors']))

    def test_missing_prediction_cannot_silently_resave_complete(self):
        record,_=self.save();(self.root/'predictions'/(record['image_id']+'.json')).unlink()
        with self.assertRaisesRegex(ValueError,'필수 예측 파일 유실'):persist_scene(self.root,self.p,self.roi,record['batch_id'],record['session_id'],self.cfg,None,record['image_id'])

    def test_invalid_prediction_is_rejected_before_any_capture(self):
        bad=copy.deepcopy(self.pred);bad['spaces'][0]['occupancy']='BROKEN'
        with self.assertRaises(ValueError):persist_scene(self.root,self.p,self.roi,'b','s',self.cfg,bad)
        self.assertFalse(list(self.root.rglob('*.png')))

    def test_prediction_schema_rejects_invalid_types_and_inconsistent_evidence(self):
        record,_=self.save()
        mutations=[('occupancy_score',float('nan')),('occupancy_score',True),('issues','bad'),('review_status','approved'),
                   ('number','99'),('number','23'),('vehicle_type','electric'),('plate_bbox',[0,0,999,3]),
                   ('quality',{'warnings':[5]}),('color','purple')]
        for field,value in mutations:
            with self.subTest(field=field,value=value):
                bad=copy.deepcopy(self.pred);bad['spaces'][0][field]=value
                with self.assertRaises(ValueError):validate_prediction(bad,record)

    def test_number_label_without_box_is_not_counted_as_exportable(self):
        record,_=self.save();rows=rows_for_edit(record)
        rows[0].update(occupancy='occupied',plate_text='23',review_status='approved');save_review(self.root,record,rows,'tester')
        report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['number_approved'],1);self.assertEqual(report['counts']['number_ready'],0)
        path,count=export_training(self.root);manifest=read_json(Path(path)/'manifest.json')
        self.assertEqual(count,1);self.assertEqual([s['task'] for s in manifest['samples']],['occupancy'])
        self.assertEqual(manifest['skipped'][0]['task'],'number')

    def test_number_box_makes_count_equal_actual_export(self):
        record,_=self.save();rows=rows_for_edit(record)
        rows[0].update(occupancy='occupied',plate_text='23',plate_bbox=[50,70,105,110],review_status='approved')
        save_review(self.root,record,rows,'tester');path,_=export_training(self.root)
        samples=read_json(Path(path)/'manifest.json')['samples'];report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['number_ready'],sum(s['task']=='number' for s in samples));self.assertEqual(report['counts']['number_ready'],1)

    def test_resave_without_new_prediction_preserves_sticker_assets(self):
        cfg,p,roi,ref=sticker_packet();pred=VisionEngine().run(p,roi,cfg,ref)
        record,_=persist_scene(self.root,p,roi,'b','s',cfg,pred)
        persist_scene(self.root,p,roi,'b','s',cfg,None,record['image_id'])
        report=DatasetIndex().scan(self.root);self.assertEqual(report['counts']['stickers'],1);self.assertFalse(report['errors'])

    def test_recent_order_uses_absolute_time_with_timezones(self):
        first,_=self.save()
        p=replace(self.p,sequence=2,timestamp_utc='2026-11-04T19:00:00+09:00')
        second,_=persist_scene(self.root,p,self.roi,'b','s',self.cfg)
        report=DatasetIndex().scan(self.root)
        self.assertEqual([e['id'] for e in report['entries']],[second['image_id'],first['image_id']])

    def test_export_scope_applies_to_both_formats(self):
        record,_=self.save();rows=rows_for_edit(record);rows[0].update(occupancy='vacant',review_status='approved')
        save_review(self.root,record,rows,'tester')
        for filters in ({'lot':'B'},{'session':'not-current'}):
            path,count=export_training(self.root,**filters);self.assertEqual(count,0)
            self.assertFalse(read_json(Path(path)/'manifest.json')['samples'])
            path,count=export_reviewed(self.root,**filters);self.assertEqual(count,0)
            self.assertEqual(Path(path).read_text(encoding='utf-8'),'')

    def test_video_source_is_preserved_despite_camera_config(self):
        p=replace(self.p,source_kind='video');cfg={**self.cfg,'capture_source':{'kind':'camera'}}
        record,_=persist_scene(self.root,p,self.roi,'b','s',cfg)
        self.assertEqual(record['source']['kind'],'video')

    def test_corrupt_saved_review_is_refused_before_ui_load(self):
        from dashboard import Worker
        record,_=self.save();rows=rows_for_edit(record);rows[0]['color']='invalid'
        atomic_json(self.root/'reviews'/(record['image_id']+'.json'),{'image_id':record['image_id'],
            'lot_id':'A','reviewer':'tester','spaces':rows,'contract_revision':record['contract_revision']})
        worker=Worker();worker.start()
        try:
            worker.jobs.put(('load',1,(self.root,record['image_id'])))
            result=worker.results.get(timeout=3);self.assertIsNone(result[2]);self.assertIsNotNone(result[3])
        finally:worker.jobs.put(None);worker.join(5)


class TransportCancellation(unittest.TestCase):
    def setUp(self):
        self.cfg,self.p,self.roi=fixture();self.p=replace(self.p,timestamp_utc=datetime.now(timezone.utc).isoformat())
        self.pred=VisionEngine().run(self.p,self.roi,self.cfg,make_reference(self.p,self.roi))
        self.cfg.update(api_contract_confirmed=True,api_nullable_bbox=True,api_nullable_confidence=True,api_camera_ids={'A':'camera-1'})
    def test_video_and_synthetic_packets_never_transmit(self):
        for kind in ('video','synthetic','import'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):build_events(replace(self.p,source_kind=kind),self.roi,self.pred,self.cfg)
    def test_cancel_drains_pending_and_invalidates_dequeued_work(self):
        sender=EventSender();event=build_events(self.p,self.roi,self.pred,self.cfg)[0]
        generation=sender.generation;sender.offer([event],2);sender.cancel();self.assertTrue(sender.jobs.empty())
        with patch('transport19.urllib.request.build_opener') as opener:
            self.assertEqual(sender.send_one(event,2,'https://test.invalid','test-only',generation),'expired')
            opener.return_value.open.assert_not_called()
    def test_cancel_during_failure_prevents_retry(self):
        sender=EventSender();event=build_events(self.p,self.roi,self.pred,self.cfg)[0];opener=MagicMock()
        def fail(*args,**kwargs):sender.cancel();raise urllib.error.URLError('synthetic failure')
        opener.open.side_effect=fail
        with patch('transport19.urllib.request.build_opener',return_value=opener):
            self.assertEqual(sender.send_one(event,2,'https://test.invalid','test-only'),'expired')
        self.assertEqual(opener.open.call_count,1)


class DashboardV20(unittest.TestCase):
    def setUp(self):
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.window=tk.Tk();self.window.withdraw()
        self.cfg,self.p,self.roi=fixture();self.app=Dashboard(self.window,self.cfg,Path(self.temp.name),PROJECT)
        self.pred=VisionEngine().run(self.p,self.roi,self.cfg,make_reference(self.p,self.roi))
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop()
        self.assertFalse(self.app.identity_worker.is_alive());self.assertFalse(self.app.worker.is_alive())
        self.temp.cleanup();self.app=None;self.window=None;gc.collect()
    def test_late_identity_is_displayed_with_its_frame_but_not_sent_over_newer_occupancy(self):
        a=self.app;a.latest_occupancy_key=('new-frame',context_key(self.roi))
        token=(a.generation,self.p,self.roi,None,('capture-batch','capture-session'))
        with patch.object(a,'send_prediction') as send:a.accept('identify',token,self.pred,None)
        send.assert_not_called();self.assertEqual(a.scene[0].timestamp_utc,self.pred['captured_at_utc'])
        self.assertEqual(a.scene_group,('capture-batch','capture-session'))
    def test_old_context_identity_cannot_restore_switched_scene(self):
        a=self.app;token=(a.generation,self.p,self.roi,None);a.clear_context()
        a.accept('identify',token,self.pred,None);self.assertIsNone(a.scene)
    def test_new_batch_waits_for_active_identity(self):
        self.app.identifying=True;before=self.app.batch
        with self.assertRaises(ValueError):self.app.new_batch()
        self.assertEqual(self.app.batch,before);self.app.identifying=False
    def test_stop_cancels_pending_network(self):
        a=self.app;before=a.sender.generation;a.live=True;a.stop_live()
        self.assertFalse(a.live);self.assertGreater(a.sender.generation,before)
    def test_export_uses_current_scope(self):
        a=self.app;a.scope.set('현재 주차장');a.lot.set('B')
        with patch.object(a,'submit') as submit:a.export_current('export')
        self.assertEqual(submit.call_args.args,('export',(a.root,'B',None)))


if __name__=='__main__':unittest.main()
