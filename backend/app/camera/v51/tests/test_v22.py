"""V22 interruption, recovery, current-label integrity and capture lifecycle."""
import copy,gc,json,tempfile,time,threading,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch,MagicMock
import tkinter as tk
import numpy as np
from test_v19 import fixture,PROJECT
from test_v20 import sticker_packet
from test_v21 import Reader
from vision19 import VisionEngine
from dataset19 import persist_scene,DatasetIndex,rebuild_scene_assets
from collection_output import read_collection,export_collection
from review import rows_for_edit,save_review
from pipeline import read_json
from parking_ai.storage import atomic_json
from records import validate_record


class DurableCollection(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.p,self.roi,self.ref=sticker_packet();engine=VisionEngine();engine.reader=Reader()
        self.pred=engine.enrich(self.p,self.roi,self.cfg,engine.run(self.p,self.roi,self.cfg,self.ref))
        self.record,_=persist_scene(self.root,self.p,self.roi,'batch','session',self.cfg,self.pred)
    def tearDown(self):self.temp.cleanup()
    def rows(self,text='23'):
        rows=rows_for_edit(self.record);row=self.pred['spaces'][0]
        rows[0].update(occupancy='occupied',plate_text=text,color='green',plate_bbox=row['plate_bbox'],sticker_polygon=row['sticker_polygon'],review_status='approved')
        return rows
    def exported(self):return export_collection(self.root)[1]['counts']['scenes']
    def label(self):
        record=read_json(self.root/'manifests'/(self.record['image_id']+'.json'));bundle=read_collection(self.root,record)
        item=next(i for i in bundle['items'] if i['kind']=='korean_label' and i['space_id']==self.roi['spaces'][0]['space_id'])
        return read_json(self.root/item['path'])
    def interrupt_resave(self):
        changed=copy.deepcopy(self.pred);changed['spaces'][0]['issues'].append('new evidence marker')
        with patch('dataset19.render_collection',side_effect=SystemExit('interrupted')) as render:
            with self.assertRaises(SystemExit):persist_scene(self.root,self.p,self.roi,self.record['batch_id'],self.record['session_id'],self.cfg,changed,self.record['image_id'])
            render.assert_called_once()
    def test_resave_interruption_blocks_old_complete_export(self):
        self.interrupt_resave();self.assertEqual(self.exported(),0)
    def test_interrupted_resave_keeps_original_and_flags_partial(self):
        before=(self.root/self.record['raw_path']).read_bytes();self.interrupt_resave();c=DatasetIndex().scan(self.root)['counts']
        self.assertEqual((c['originals'],c['partial'],c['collection_pairs']),(1,1,0));self.assertEqual(before,(self.root/self.record['raw_path']).read_bytes())
    def test_review_commit_interruption_cannot_export_old_label(self):
        with patch('collection_output.render_collection',side_effect=SystemExit('interrupted')):
            with self.assertRaises(SystemExit):save_review(self.root,self.record,self.rows(),'tester')
        self.assertEqual(self.exported(),0);self.assertEqual(read_json(self.root/'reviews'/(self.record['image_id']+'.json'))['spaces'][0]['review_status'],'approved')
    def test_completed_bundle_but_pending_manifest_is_not_exported(self):
        record=copy.deepcopy(self.record);record['collection_status']='pending';atomic_json(self.root/'manifests'/(record['image_id']+'.json'),record)
        self.assertEqual(self.exported(),0)
    def test_partial_unmarked_asset_blocks_automatic_export(self):
        record=copy.deepcopy(self.record);record['asset_status']='partial';atomic_json(self.root/'manifests'/(record['image_id']+'.json'),record)
        self.assertEqual(self.exported(),0)
    def test_repair_resave_preserves_identity_time_source_and_raw(self):
        before=(self.root/self.record['raw_path']).read_bytes();self.interrupt_resave();record,receipt=rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
        self.assertEqual(receipt['status'],'complete');self.assertEqual(self.exported(),1);self.assertEqual(before,(self.root/record['raw_path']).read_bytes())
        for key in ('image_id','captured_at_utc','frame_id','stream_epoch','batch_id','session_id','source','raw_sha256'):self.assertEqual(record[key],self.record[key])
        self.assertIn('new evidence marker',self.label()['확인필요사유'])
    def test_repair_interrupted_human_review_uses_current_truth(self):
        with patch('collection_output.render_collection',side_effect=SystemExit('interrupted')):
            with self.assertRaises(SystemExit):save_review(self.root,self.record,self.rows(),'tester')
        rebuild_scene_assets(self.root,self.record['image_id'],self.cfg);self.assertTrue(self.label()['사람검수완료']);self.assertEqual(self.exported(),1)
    def test_repair_refuses_corrupt_original(self):
        (self.root/self.record['raw_path']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
    def test_repair_refuses_missing_promised_prediction(self):
        (self.root/'predictions'/(self.record['image_id']+'.json')).unlink()
        with self.assertRaises(ValueError):rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
    def test_repair_refuses_invalid_review_without_guessing(self):
        atomic_json(self.root/'reviews'/(self.record['image_id']+'.json'),{'broken':True})
        with self.assertRaises((KeyError,ValueError)):rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
    def test_old_collection_requires_rebuild_then_exports(self):
        path=self.root/'bundles'/(self.record['image_id']+'.json');bundle=read_json(path);bundle['schema']='korean-collection-v1';bundle.pop('input_fingerprints');atomic_json(path,bundle)
        self.assertEqual(self.exported(),0);rebuild_scene_assets(self.root,self.record['image_id'],self.cfg);self.assertEqual(self.exported(),1)
    def test_whitespace_human_number_stays_numeric(self):
        save_review(self.root,self.record,self.rows(' 23 '),'tester');data=self.label()
        self.assertEqual(data['차량번호'],23);self.assertEqual(data['차량번호표기'],'23');self.assertTrue(data['사람검수완료'])
    def test_valid_external_prediction_change_invalidates_cached_bundle(self):
        index=DatasetIndex();self.assertEqual(index.scan(self.root)['counts']['collection_pairs'],2)
        pred=copy.deepcopy(self.pred);pred['spaces'][0]['issues'].append('external update');atomic_json(self.root/'predictions'/(self.record['image_id']+'.json'),pred)
        self.assertEqual(index.scan(self.root)['counts']['collection_pairs'],0);self.assertEqual(self.exported(),0)
    def test_external_review_deletion_invalidates_bundle(self):
        save_review(self.root,self.record,self.rows(),'tester');(self.root/'reviews'/(self.record['image_id']+'.json')).unlink();self.assertEqual(self.exported(),0)
    def test_invalid_completion_status_rejected(self):
        bad=copy.deepcopy(self.record);bad['collection_status']='anything'
        with self.assertRaises(ValueError):validate_record(bad)
    def test_render_failure_preserves_approved_truth_but_blocks_export(self):
        with patch('collection_output.annotate',side_effect=OSError('font/disk failure')):save_review(self.root,self.record,self.rows(),'tester')
        self.assertEqual(self.exported(),0)
        self.assertEqual(read_json(self.root/'reviews'/(self.record['image_id']+'.json'))['spaces'][0]['review_status'],'approved')
    def test_repair_is_idempotent_in_logical_counts(self):
        for i in range(2):rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
        c=DatasetIndex().scan(self.root)['counts'];self.assertEqual((c['originals'],c['collection_pairs'],c['partial']),(1,2,0))


class LifecycleUI(unittest.TestCase):
    def setUp(self):
        from dashboard import Dashboard
        self.temp=tempfile.TemporaryDirectory();self.window=tk.Tk();self.window.withdraw();self.cfg,self.p,self.roi=fixture()
        self.app=Dashboard(self.window,self.cfg,Path(self.temp.name),PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop();self.temp.cleanup();self.app=None;self.window=None;gc.collect()
    def test_new_batch_blocked_while_calibrating(self):
        a=self.app;a.auto_run=True;a.auto_phase='calibrating';a.calibrating=True;generation=a.generation
        with self.assertRaises(ValueError):a.new_batch()
        self.assertEqual(a.generation,generation);a.calibrating=False
    def test_new_batch_while_waiting_cancels_auto_request(self):
        a=self.app;a.auto_run=True;a.auto_phase='waiting';a.auto.set(True);a.new_batch()
        self.assertFalse(a.auto_run or a.auto.get());self.assertEqual(a.auto_phase,'off')
    def test_input_change_resets_automatic_controls_and_status(self):
        a=self.app;a.auto_run=True;a.auto.set(True);a.clear_context();self.assertFalse(a.auto_run or a.auto.get());self.assertIn('정지',a.auto_status.get())
    def test_settings_change_resets_auto_checkbox(self):
        a=self.app;a.auto_run=True;a.auto.set(True);a.invalidate_inference();self.assertFalse(a.auto.get())
    def test_export_serialized_without_filling_queue(self):
        a=self.app
        with patch.object(a,'submit') as submit:
            a.export_current('collection_export')
            with self.assertRaises(ValueError):a.export_current('collection_export')
        self.assertEqual(submit.call_count,1);a.accept('collection_export',None,None,'disk failure');self.assertFalse(a.exporting)
    def test_root_change_blocked_during_export(self):
        a=self.app;a.exporting=True
        with patch('dashboard.filedialog.askdirectory') as dialog:
            with self.assertRaises(ValueError):a.choose_root()
            dialog.assert_not_called()
    def test_roi_editor_pauses_before_modal_and_invalidates_old_result(self):
        a=self.app;a.scene=(self.p,self.roi);a.roi=self.roi;a.auto_run=True;a.auto.set(True);generation=a.generation
        def dialog(*args,**kwargs):
            self.assertFalse(a.auto_run or a.live or a.auto.get());self.assertGreater(a.generation,generation)
            return MagicMock(result=None)
        with patch('dashboard.RoiEditor',side_effect=dialog),patch.object(self.window,'wait_window'):a.edit_roi()
    def test_open_saved_scene_stops_auto_registration(self):
        a=self.app;a.auto_run=True;a.auto_phase='waiting';a.files.insert('','end',iid='sample');a.files.selection_set('sample')
        with patch.object(a,'submit'):a.open_selected()
        self.assertFalse(a.auto_run);self.assertTrue(a.loading)
    def test_repair_selected_routes_to_io_worker_and_stops_live(self):
        a=self.app;a.auto_run=True;a.live=True;a.files.insert('','end',iid='sample');a.files.selection_set('sample')
        with patch.object(a,'submit') as submit:a.repair_selected()
        self.assertEqual(submit.call_args.args[0],'repair');self.assertTrue(a.repairing);self.assertFalse(a.auto_run or a.live)


def until(fn,seconds=3):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.005)
    raise AssertionError('camera mock did not reach expected state')


class CameraLifecycle(unittest.TestCase):
    def setUp(self):
        from camera_hub import CameraHub,CameraSpec
        self.closed=threading.Event();owner=self
        class Source:
            def open(self):pass
            def read(self):time.sleep(.01);return np.full((80,100,3),85,np.uint8)
            def apply_quality(self,changes):return dict(changes)
            def close(self):owner.closed.set()
        self.hub=CameraHub([CameraSpec('A',serial='mock')],source_factory=lambda spec:Source(),retry_seconds=.01);self.hub.start()
        self.packet=until(lambda:self.hub.get_latest_frame('A'))
    def tearDown(self):self.hub.stop();self.assertTrue(self.closed.is_set());self.assertTrue(self.hub.is_stopped())
    def test_reader_receives_private_pixels(self):
        self.packet.image[:]=0;self.assertTrue((self.hub.get_latest_frame('A').image==85).all())
    def test_quality_change_advances_epoch_and_returns_new_frame(self):
        receipt=self.hub.request_quality('A',{'exposure':123});self.assertTrue(receipt.get(timeout=2)['ok'])
        p=until(lambda:(p if p and p.stream_epoch>self.packet.stream_epoch else None) if (p:=self.hub.get_latest_frame('A')) else None)
        self.assertGreater(p.sequence,self.packet.sequence)
    def test_shutdown_never_returns_cached_frame(self):
        self.hub.request_stop();self.assertIsNone(self.hub.get_latest_frame('A'))
    def test_stale_frame_not_delivered(self):
        state=self.hub._states['A']
        with state.lock:state.latest=replace(state.latest,received_monotonic=time.monotonic()-20)
        # Hold the source publication lock while calling through the same thread
        # is impossible; stop the producer first, then exercise a non-shutdown hub.
        self.hub.stop();self.hub._shutdown.clear()
        with state.lock:state.status='RUNNING';state.latest=replace(self.packet,received_monotonic=time.monotonic()-20)
        self.assertIsNone(self.hub.get_latest_frame('A'))


if __name__=='__main__':unittest.main()
