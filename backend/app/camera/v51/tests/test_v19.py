import copy
import gc
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch,MagicMock
from datetime import datetime,timezone,timedelta
import urllib.error
import cv2
import numpy as np
from camera_hub import FramePacket,load_camera_specs
from layouts import layout,snapshot,validate_spaces
from contracts import number_config_for_lot,CONTRACT_REVISION,D39_NUMBERS
from analysis import number
from vision19 import crop_space,make_reference,reference_difference,VisionEngine,sticker_region,geometry_candidates,context_key
from dataset19 import persist_scene,DatasetIndex,export_training,validate_prediction
from pipeline import read_json
from review import save_review,rows_for_edit,review_revision,export_reviewed
from parking_ai.storage import atomic_json

PROJECT=Path(__file__).resolve().parents[1]


def fixture(lot='A'):
    cfg=read_json(PROJECT/'config.json');cfg.update(project=str(PROJECT),enable_ocr=False)
    # Fixtures introduced by V19 exercise the historic D-39 data contract.
    cfg.pop('physical_sticker_set',None)
    image=np.full((300,360,3),175,np.uint8)
    catalog={s['space_id']:s for s in layout(lot)['spaces']}
    first='A-05' if lot=='A' else 'A-07'
    spaces=[{**catalog[first],'polygon':[[20,20],[145,20],[145,260],[20,260]]},
            {**catalog['B-01'],'polygon':[[185,20],[280,20],[320,260],[225,260]]}]
    packet=FramePacket('A','test-camera',1,'2026-11-04T09:00:00+00:00',time.monotonic(),image,stream_epoch=1)
    roi=snapshot(lot,'A','test-camera',[360,300],'test-pose',spaces)
    return cfg,packet,roi


class GeometryAndRules(unittest.TestCase):
    def test_rectangular_and_slanted_quads(self):
        _,p,r=fixture('B');validate_spaces('B',r['spaces'],[360,300]);crop,mask,origin=crop_space(p.image,r['spaces'][1]['polygon'])
        self.assertEqual(origin,(185,20));self.assertTrue(np.all(crop[mask==0]==255));self.assertTrue((mask==0).any())
    def test_context_changes_for_lot_pose_and_polygon(self):
        _,p,r=fixture();key=context_key(r)
        for field,value in [('lot_id','B'),('pose','moved')]:
            modified=copy.deepcopy(r);modified[field]=value;self.assertNotEqual(key,context_key(modified))
        modified=copy.deepcopy(r);modified['spaces'][0]['polygon'][0][0]+=1;self.assertNotEqual(key,context_key(modified))
    def test_full_layout_sizes(self):
        self.assertEqual([layout(l)['space_count'] for l in 'ABCD'],[12,16,12,64])
    def test_single_camera_config(self):
        self.assertEqual(len(load_camera_specs(PROJECT/'cameras.json')),1)
    def test_d39_and_range(self):
        cfg=number_config_for_lot('A',{})
        for n in D39_NUMBERS:self.assertEqual(number(n,cfg),n)
        for n in ['01','09','100','49','87','9','23x']:self.assertIsNone(number(n,cfg))
        self.assertEqual(number('10',{}),'10');self.assertEqual(number('99',{}),'99')
    def test_virtual_numbers_separate(self):
        self.assertEqual(number('17가1724',number_config_for_lot('C',{})),'1724')
    def test_slanted_outside_candidate(self):
        _,p,r=fixture();mask=np.zeros(p.image.shape[:2],np.uint8);cv2.rectangle(mask,(120,70),(210,130),255,-1)
        warnings,_=geometry_candidates(mask,r['spaces']);self.assertTrue(warnings);self.assertEqual(warnings[0]['source'],'background_change_not_vehicle_mask')
    def test_sticker_background_not_car_color(self):
        _,p,r=fixture();image=p.image.copy();cv2.rectangle(image,(45,65),(125,125),(0,200,0),-1)
        cv2.putText(image,'23',(60,105),cv2.FONT_HERSHEY_SIMPLEX,.9,(0,0,0),2)
        poly,color=sticker_region(image,r['spaces'][0]['polygon'],[59,80,103,108])
        self.assertEqual(color,'green');self.assertIsNotNone(poly)
    def test_no_sticker_is_unknown(self):
        _,p,r=fixture();self.assertEqual(sticker_region(p.image,r['spaces'][0]['polygon'],[59,80,103,108]),(None,'unknown'))


class Inference(unittest.TestCase):
    def test_reference_missing_does_not_say_empty(self):
        cfg,p,r=fixture();pred=VisionEngine().run(p,r,cfg);self.assertTrue(all(x['occupancy']=='unknown' for x in pred['spaces']))
    def test_empty_and_changed_occupancy(self):
        cfg,p,r=fixture();ref=make_reference(p,r);image=p.image.copy();image[55:210,40:125]=(40,40,40)
        pred=VisionEngine().run(replace(p,image=image,sequence=2),r,cfg,ref)
        self.assertEqual([x['occupancy'] for x in pred['spaces']],['occupied','vacant'])
    def test_occupancy_without_number_remains_occupied(self):
        cfg,p,r=fixture();ref=make_reference(p,r);image=p.image.copy();image[55:210,40:125]=50
        row=VisionEngine().run(replace(p,image=image),r,cfg,ref)['spaces'][0]
        self.assertEqual(row['occupancy'],'occupied');self.assertIsNone(row['number'])
    def test_changed_epoch_rejects_reference(self):
        cfg,p,r=fixture();ref=make_reference(p,r);pred=VisionEngine().run(replace(p,stream_epoch=2),r,cfg,ref)
        self.assertTrue(all(x['occupancy']=='unknown' for x in pred['spaces']))
    def test_black_frame_is_not_empty(self):
        cfg,p,r=fixture();ref=make_reference(p,r);pred=VisionEngine().run(replace(p,image=np.zeros_like(p.image)),r,cfg,ref)
        self.assertTrue(all(x['occupancy']=='unknown' for x in pred['spaces']))
    def test_no_classifier_no_silent_fallback(self):
        cfg,p,r=fixture();pred=VisionEngine().run(p,r,cfg,make_reference(p,r),'classifier')
        self.assertTrue(all(x['occupancy']=='unknown' for x in pred['spaces']));self.assertEqual(pred['mode'],'classifier')
    def test_predictions_do_not_create_approvals(self):
        cfg,p,r=fixture();pred=VisionEngine().run(p,r,cfg,make_reference(p,r))
        self.assertTrue(all(x['review_status']=='pending' for x in pred['spaces']))
    def test_reference_not_serialized_as_image(self):
        cfg,p,r=fixture();pred=VisionEngine().run(p,r,cfg,make_reference(p,r));json.dumps(pred,allow_nan=False)


class Storage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=fixture();self.pred=VisionEngine().run(self.p,self.roi,self.cfg,make_reference(self.p,self.roi))
    def tearDown(self):self.temp.cleanup()
    def save(self):return persist_scene(self.root,self.p,self.roi,'same-scene','session',self.cfg,self.pred)
    def test_save_counts_real_assets(self):
        record,receipt=self.save();report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['spaces'],2);self.assertEqual(report['counts']['occupancy_approved'],0);self.assertEqual(receipt['status'],'complete')
    def test_retry_same_scene_not_new_original(self):
        record,_=self.save();persist_scene(self.root,self.p,self.roi,'same-scene','session',self.cfg,self.pred,record['image_id'])
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['originals'],1)
    def test_wrong_frame_cannot_replace_original(self):
        record,_=self.save()
        with self.assertRaises(ValueError):persist_scene(self.root,replace(self.p,sequence=9),self.roi,'same-scene','session',self.cfg,self.pred,record['image_id'])
    def test_same_frame_changed_pixels_cannot_replace_crops(self):
        record,_=self.save();changed=self.p.image.copy();changed[50:80]=0
        with self.assertRaises(ValueError):persist_scene(self.root,replace(self.p,image=changed),self.roi,'same-scene','session',self.cfg,self.pred,record['image_id'])
    def test_invalid_review_does_not_hide_valid_original(self):
        record,_=self.save();atomic_json(self.root/'reviews'/(record['image_id']+'.json'),{'broken':True})
        report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['label_files'],0);self.assertTrue(report['errors'])
    def test_reanalysis_keeps_history_without_duplicating_counts(self):
        record,_=self.save();new=copy.deepcopy(self.pred);new['elapsed_ms']+=1
        persist_scene(self.root,self.p,self.roi,'same-scene','session',self.cfg,new,record['image_id'])
        self.assertEqual(len(list((self.root/'prediction_history'/record['image_id']).glob('*.json'))),1)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['originals'],1)
    def test_partial_write_not_complete(self):
        with patch('dataset19.write_png',side_effect=OSError('disk full')):record,receipt=self.save()
        self.assertEqual(receipt['status'],'partial');report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['originals'],1);self.assertEqual(report['counts']['spaces'],0);self.assertEqual(report['counts']['partial'],1)
    def test_missing_crop_detected_after_restart(self):
        record,receipt=self.save();(self.root/receipt['items'][0]['path']).unlink()
        report=DatasetIndex().scan(self.root);self.assertEqual(report['counts']['spaces'],1);self.assertTrue(report['errors'])
    def test_modified_original_detected(self):
        record,_=self.save();(self.root/record['raw_path']).write_bytes(b'bad')
        report=DatasetIndex().scan(self.root);self.assertEqual(report['counts']['originals'],0);self.assertTrue(report['errors'])
    def test_prediction_wrong_context_rejected(self):
        record,_=self.save();pred=copy.deepcopy(self.pred);pred['frame_id']=19
        with self.assertRaises(ValueError):validate_prediction(pred,record)
    def test_labels_and_prediction_separate(self):
        record,_=self.save();before=(self.root/'predictions'/(record['image_id']+'.json')).read_bytes()
        rows=rows_for_edit(record);rows[0].update(occupancy='occupied',plate_text='23',review_status='approved')
        save_review(self.root,record,rows,'tester');report=DatasetIndex().scan(self.root)
        self.assertEqual(report['counts']['label_rows'],2);self.assertEqual(report['counts']['number_approved'],1);self.assertEqual(before,(self.root/'predictions'/(record['image_id']+'.json')).read_bytes())
    def test_conflicting_review_refused(self):
        record,_=self.save();rows=rows_for_edit(record);save_review(self.root,record,rows,'one',None)
        with self.assertRaises(ValueError):save_review(self.root,record,rows,'two',None)
    def test_legacy_approval_requires_review(self):
        record,_=self.save();rows=rows_for_edit(record);rows[0].update(occupancy='occupied',plate_text='23',review_status='approved')
        old={'image_id':record['image_id'],'lot_id':'A','contract_revision':'old','spaces':rows}
        self.assertEqual(rows_for_edit(record,old)[0]['review_status'],'pending')
    def test_training_crops_share_group_split(self):
        record,_=self.save();rows=rows_for_edit(record)
        for row in rows:row.update(occupancy='vacant',review_status='approved')
        save_review(self.root,record,rows,'tester');path,count=export_training(self.root);manifest=read_json(Path(path)/'manifest.json')
        self.assertEqual(count,2);self.assertEqual(len({x['split'] for x in manifest['samples']}),1);self.assertTrue(manifest['warnings'])
    def test_existing_image_export_revalidated(self):
        record,_=self.save();rows=rows_for_edit(record);rows[0].update(occupancy='vacant',review_status='approved');save_review(self.root,record,rows,'tester')
        path,count=export_reviewed(self.root);self.assertEqual(count,1);self.assertTrue(path.with_suffix('.images').exists())
    def test_scope_filters(self):
        self.save();self.assertEqual(DatasetIndex().scan(self.root,lot='B')['counts']['originals'],0)
        self.assertEqual(DatasetIndex().scan(self.root,session='other')['counts']['originals'],0)


class DashboardUI(unittest.TestCase):
    def setUp(self):
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.window=tk.Tk();self.window.withdraw();self.cfg,self.p,self.roi=fixture()
        self.app=Dashboard(self.window,self.cfg,Path(self.temp.name),PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop();self.temp.cleanup()
        self.app=None;self.window=None;gc.collect()
    def test_all_cards_12_and_16(self):
        self.assertEqual(len(self.app.cards),12);self.app.lot.set('B');self.app.change_lot();self.assertEqual(len(self.app.cards),16)
    def test_stale_analysis_does_not_change_scene(self):
        pred=VisionEngine().run(self.p,self.roi,self.cfg)
        self.app.generation=2;self.app.accept('analyze',(1,self.p,self.roi,None),pred,None);self.assertIsNone(self.app.scene)
    def test_same_frame_selection(self):
        self.app.roi=self.roi;self.app.scene=(self.p,self.roi);self.app.pred=VisionEngine().run(self.p,self.roi,self.cfg)
        self.app.select_slot('A-05');self.assertEqual(self.app.selected,'A-05');self.assertIn('A-05',self.app.detail.get())
    def test_switch_clears_reference_and_predictions(self):
        self.app.reference=make_reference(self.p,self.roi);self.app.pred={'spaces':[]};self.app.lot.set('B');self.app.change_lot()
        self.assertIsNone(self.app.reference);self.assertIsNone(self.app.pred)
    def test_saved_scene_cannot_authorize_live_capture(self):
        self.app.input_mode='saved'
        with self.assertRaises(ValueError):self.app.confirm()
    def test_partial_roi_cannot_start_collection(self):
        self.app.roi=self.roi;self.app.confirmed_epoch=1
        with self.assertRaisesRegex(ValueError,'12'):self.app.check_ready(self.p)
    def test_failed_review_is_not_shown_as_saved_approval(self):
        self.app.rows=[{'space_id':'A-05','review_status':'approved'}]
        self.app.accept('review',None,None,'disk full')
        self.assertEqual(self.app.rows[0]['review_status'],'pending');self.assertTrue(self.app.dirty)
    def test_reconnect_confirmation_uses_current_frame(self):
        self.app.roi=self.roi;self.app.scene=(self.p,self.roi);self.app.reference=make_reference(self.p,self.roi)
        current=replace(self.p,sequence=2,stream_epoch=2)
        hub=MagicMock();hub.get_latest_frame.return_value=current;hub.is_stopped.return_value=True
        self.app.hub=hub;self.app.confirm()
        self.assertEqual(self.app.scene[0].sequence,2);self.assertEqual(self.app.confirmed_epoch,2);self.assertIsNone(self.app.reference)
    def test_full_b_scene_analysis_save_approve_and_export(self):
        image=np.full((400,1000,3),175,np.uint8);spaces=[]
        for i,s in enumerate(layout('B')['spaces']):
            x=10+(i%8)*120;y=10+(i//8)*190
            spaces.append({**s,'polygon':[[x+15,y],[x+100,y],[x+85,y+165],[x,y+165]]})
        roi=snapshot('B','IMPORT','test-image',[1000,400],'test-pose',spaces)
        packet=replace(self.p,camera_id='IMPORT',serial='test-image',image=image)
        a=self.app;a.lot.set('B');a.input_mode='image';a.raw=packet;a.roi=roi;a.confirm();a.mode.set('A안 빈 기준')
        a.reference=make_reference(packet,roi);a.analyze_once();a.accept(*a.worker.results.get(timeout=5))
        self.assertEqual(len(a.pred['spaces']),16);a.save_scene()
        def until(kind):
            for _ in range(8):
                result=a.io_worker.results.get(timeout=5);a.accept(*result)
                if result[0]==kind:
                    self.assertIsNone(result[3]);return
            self.fail('missing '+kind)
        until('save');self.assertIsNotNone(a.record)
        a.select_slot(spaces[0]['space_id']);a.occ.set('vacant');a.persist_review(True);until('review')
        report=DatasetIndex().scan(a.root)
        self.assertEqual(report['counts']['spaces'],16);self.assertEqual(report['counts']['originals'],1)
        self.assertEqual(report['counts']['occupancy_approved'],1)
        path,count=export_training(a.root);self.assertEqual(count,1);self.assertTrue((Path(path)/'manifest.json').is_file())


class Transport(unittest.TestCase):
    def setUp(self):
        from transport19 import build_events,EventSender
        self.build=build_events;self.sender=EventSender();self.cfg,self.p,self.roi=fixture()
        self.p=replace(self.p,timestamp_utc=datetime.now(timezone.utc).isoformat())
        self.pred=VisionEngine().run(self.p,self.roi,self.cfg,make_reference(self.p,self.roi))
        self.cfg.update(api_contract_confirmed=True,api_nullable_bbox=True,api_nullable_confidence=True,api_camera_ids={'A':'camera-1'})
    def test_disabled_without_confirmed_contract(self):
        self.cfg['api_contract_confirmed']=False
        with self.assertRaises(ValueError):self.build(self.p,self.roi,self.pred,self.cfg)
    def test_imported_image_never_sent(self):
        with self.assertRaises(ValueError):self.build(replace(self.p,camera_id='IMPORT'),self.roi,self.pred,self.cfg)
    def test_unknown_occupancy_not_sent(self):
        for row in self.pred['spaces']:row['occupancy']='unknown'
        self.assertEqual(self.build(self.p,self.roi,self.pred,self.cfg),[])
    def test_ev_translation_and_no_fabricated_bbox_score(self):
        evidence={'text':'23','number_candidate':'23','score':.95,'text_bbox':[60,80,103,108],'status':'candidate','method':'sticker_color'}
        self.pred['spaces'][0].update(occupancy='occupied',number='23',vehicle_type='electric',color='green',
            plate_bbox=[60,80,103,108],sticker_polygon=[[45,65],[125,65],[125,125],[45,125]],zone_rule='allowed',
            ocr_status='candidate',selected_ocr=evidence,ocr_candidates=[evidence,{**evidence,'method':'sticker_value_binary'}])
        event=self.build(self.p,self.roi,self.pred,self.cfg)[0]
        self.assertEqual(event['vehicle_type'],'ev');self.assertIsNone(event['bbox_xyxy']);self.assertIsNone(event['confidence'])
    def test_old_observation_rejected(self):
        old=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
        self.pred['captured_at_utc']=old
        with self.assertRaises(ValueError):self.build(replace(self.p,timestamp_utc=old),self.roi,self.pred,self.cfg)
    def test_retry_keeps_same_event_id_and_payload(self):
        event=self.build(self.p,self.roi,self.pred,self.cfg)[0]
        opener=MagicMock();response=MagicMock();response.__enter__.return_value.status=202
        opener.open.side_effect=[urllib.error.URLError('test'),response]
        with patch('transport19.urllib.request.build_opener',return_value=opener):result=self.sender.send_one(event,2,'https://test.invalid','test-only')
        self.assertEqual(result,'accepted');self.assertEqual(opener.open.call_count,2)
        self.assertEqual(opener.open.call_args_list[0].args[0].data,opener.open.call_args_list[1].args[0].data)
    def test_unauthorized_is_not_retried(self):
        event=self.build(self.p,self.roi,self.pred,self.cfg)[0];opener=MagicMock()
        opener.open.side_effect=urllib.error.HTTPError('https://test.invalid',401,'test',{},None)
        with patch('transport19.urllib.request.build_opener',return_value=opener):result=self.sender.send_one(event,2,'https://test.invalid','test-only')
        self.assertEqual(result,'HTTP 401');self.assertEqual(opener.open.call_count,1)
    def test_redirects_disabled(self):
        from transport19 import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))
    def test_queue_keeps_only_latest_observation(self):
        self.sender.offer(['old'],2);self.sender.offer(['new'],2)
        self.assertEqual(self.sender.jobs.qsize(),1);self.assertEqual(self.sender.jobs.get()[0],['new'])
    def test_stop_prevents_request(self):
        self.sender.stop_event.set();event=self.build(self.p,self.roi,self.pred,self.cfg)[0]
        with patch('transport19.urllib.request.build_opener') as opener:
            self.assertEqual(self.sender.send_one(event,2,'https://test.invalid','test-only'),'expired')
            opener.return_value.open.assert_not_called()


if __name__=='__main__':unittest.main()
