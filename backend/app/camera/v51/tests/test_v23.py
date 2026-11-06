"""A/B display, previous-dataset reuse and transition integrity regressions."""
import copy,gc,hashlib,tempfile,time,unittest
from pathlib import Path
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import tkinter as tk
from test_v19 import fixture,PROJECT
from demo import build_demo_scene,DemoDashboard
from dashboard import Dashboard
from dataset19 import persist_scene
from pipeline import read_json
from parking_ai.storage import atomic_json
from saved_scenes import load_saved_scene,load_latest_lot
from prediction_contract import validate_prediction


def fingerprints(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def save_sample(root,lot='A'):
    cfg,_,_=fixture(lot);cfg['capture_source']={'kind':'synthetic','synthetic':True}
    packet,roi,ref,pred=build_demo_scene(lot,cfg)
    record,_=persist_scene(root,packet,roi,'batch-'+lot,'session-'+lot,cfg,pred)
    return cfg,record


class DemoLayouts(unittest.TestCase):
    def test_both_shapes_use_existing_catalog_and_real_contract(self):
        for lot,count in [('A',12),('B',16)]:
            cfg,_,_=fixture(lot);packet,roi,_,pred=build_demo_scene(lot,cfg)
            self.assertEqual(len(roi['spaces']),count);self.assertEqual(packet.source_kind,'synthetic')
            self.assertEqual(len(pred['spaces']),count)
            validate_prediction(pred,{'roi':roi,'frame_id':packet.sequence,'captured_at_utc':packet.timestamp_utc,
                                     'lot_id':lot,'stream_epoch':packet.stream_epoch,'image_size':list(packet.image.shape[1::-1])})
            for slot in roi['spaces']:
                self.assertEqual(slot['polygon'][0][0]==slot['polygon'][3][0],lot=='A')
            self.assertTrue(all(row['number'] is None for row in pred['spaces']))
    def test_demo_does_not_modify_passed_configuration(self):
        cfg,_,_=fixture();before=copy.deepcopy(cfg);build_demo_scene('A',cfg);self.assertEqual(cfg,before)


class PreviousDatasets(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg,self.a=save_sample(self.root,'A');_,self.b=save_sample(self.root,'B')
    def tearDown(self):self.temp.cleanup()
    def test_a_and_b_reopen_with_correct_counts_and_no_file_changes(self):
        before=fingerprints(self.root)
        for lot,count in [('A',12),('B',16)]:
            result=load_latest_lot(self.root,lot);record,image,pred,review=result['scene']
            self.assertEqual(record['lot_id'],lot);self.assertEqual(len(record['roi']['spaces']),count);self.assertEqual(len(pred['spaces']),count)
            self.assertFalse(result['warnings'])
        self.assertEqual(before,fingerprints(self.root))
    def test_missing_a_never_returns_b(self):
        (self.root/'manifests'/(self.a['image_id']+'.json')).unlink()
        self.assertIsNone(load_latest_lot(self.root,'A')['scene'])
        self.assertEqual(load_latest_lot(self.root,'B')['scene'][0]['lot_id'],'B')
    def test_latest_capture_chosen_and_b_excluded(self):
        _,new=save_sample(self.root,'A')
        self.assertEqual(load_latest_lot(self.root,'A')['scene'][0]['image_id'],new['image_id'])
    def test_corrupt_latest_falls_back_with_visible_warning(self):
        _,new=save_sample(self.root,'A');(self.root/new['raw_path']).write_bytes(b'broken')
        result=load_latest_lot(self.root,'A')
        self.assertEqual(result['scene'][0]['image_id'],self.a['image_id']);self.assertEqual(len(result['warnings']),1)
    def test_empty_prediction_is_rejected_not_treated_as_absent(self):
        atomic_json(self.root/'predictions'/(self.a['image_id']+'.json'),{})
        with self.assertRaises((ValueError,KeyError,TypeError)):load_saved_scene(self.root,self.a['image_id'])
    def test_legacy_bundle_can_be_viewed_without_migration(self):
        path=self.root/'bundles'/(self.a['image_id']+'.json');data=read_json(path);data['schema']='korean-collection-v1';data.pop('input_fingerprints');atomic_json(path,data)
        before=fingerprints(self.root);result=load_latest_lot(self.root,'A')
        self.assertEqual(result['scene'][0]['image_id'],self.a['image_id']);self.assertEqual(before,fingerprints(self.root))
    def test_missing_pending_prediction_is_rejected(self):
        record=copy.deepcopy(self.a);record['prediction_status']='pending';atomic_json(self.root/'manifests'/(record['image_id']+'.json'),record)
        (self.root/'predictions'/(record['image_id']+'.json')).unlink()
        with self.assertRaises(ValueError):load_saved_scene(self.root,record['image_id'])
    def test_invalid_review_is_reported(self):
        atomic_json(self.root/'reviews'/(self.a['image_id']+'.json'),{})
        result=load_latest_lot(self.root,'A');self.assertIsNone(result['scene']);self.assertEqual(len(result['warnings']),1)


class LotDisplay(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,_,_=fixture()
        self.window=tk.Tk();self.window.withdraw();self.app=Dashboard(self.window,self.cfg,self.root,PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.loading=False;self.app.close();self.window.mainloop()
        self.app=None;self.window=None;gc.collect();self.temp.cleanup()
    def wait_ready(self):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            self.window.update()
            if not self.app.loading and not self.app.scanning:return
            time.sleep(.01)
        self.fail('background read/scan timeout')
    def test_initial_a_catalog_twelve_and_six_columns(self):
        a=self.app;self.assertEqual(len(a.cards),12);self.assertEqual(len(a.canvas.find_withtag('catalog_slot')),12)
        self.assertEqual((a.cards['A-06'].grid_info()['row'],a.cards['A-06'].grid_info()['column']),(0,5))
        self.assertEqual((a.cards['B-01'].grid_info()['row'],a.cards['B-01'].grid_info()['column']),(1,0))
        self.assertIsNone(a.roi)
    def test_b_to_a_clears_stale_photo_and_detail(self):
        a=self.app;a.lot.set('B');a.change_lot();packet,roi,_,pred=build_demo_scene('B',self.cfg)
        a.raw=packet;a.roi=roi;a.pred=pred;a.input_mode='image';a.select_slot('A-01');a.number.set('23')
        a.lot.set('A');a.change_lot()
        self.assertIsNone(a.raw);self.assertIsNone(a.scene);self.assertIsNone(a.roi);self.assertIsNone(a.pred)
        self.assertEqual(a.number.get(),'');self.assertEqual(a.detail.get(),'칸을 선택하세요.');self.assertEqual(len(a.cards),12)
        self.assertEqual(len(a.canvas.find_withtag('catalog_slot')),12)
    def test_b_cards_have_eight_columns(self):
        a=self.app;a.lot.set('B');a.change_lot();self.assertEqual(len(a.cards),16)
        self.assertEqual((a.cards['B-01'].grid_info()['row'],a.cards['B-01'].grid_info()['column']),(1,0))
        self.assertEqual(len(a.canvas.find_withtag('catalog_slot')),16)
    def test_busy_change_restores_previous_b_without_roi(self):
        a=self.app;a.lot.set('B');a.change_lot();a.loading=True;a.lot.set('A')
        with self.assertRaises(ValueError):a.change_lot()
        self.assertEqual(a.lot.get(),'B');self.assertEqual(len(a.cards),16)
    def test_cancel_unsaved_change_restores_previous_b_without_roi(self):
        a=self.app;a.lot.set('B');a.change_lot();a.dirty=True;a.lot.set('A')
        with patch('dashboard.messagebox.askyesno',return_value=False):self.assertFalse(a.change_lot())
        self.assertEqual(a.lot.get(),'B')
    def test_view_saved_b_then_choose_a_loads_a_twelve(self):
        a=self.app;save_sample(self.root,'A');save_sample(self.root,'B');self.wait_ready()
        a.lot.set('B');a.change_lot();a.open_latest_lot();self.wait_ready();self.assertEqual(a.record['lot_id'],'B')
        before=fingerprints(self.root);a.lot.set('A');a.change_lot();self.wait_ready()
        self.assertEqual(a.record['lot_id'],'A');self.assertEqual(len(a.roi['spaces']),12);self.assertEqual(len(a.cards),12)
        self.assertEqual(a.input_mode,'saved');self.assertEqual(before,fingerprints(self.root))
    def test_no_a_scene_shows_catalog_not_previous_b(self):
        a=self.app;save_sample(self.root,'B');self.wait_ready();a.lot.set('B');a.change_lot();a.open_latest_lot();self.wait_ready()
        a.lot.set('A');a.change_lot();self.wait_ready()
        self.assertIsNone(a.scene);self.assertIsNone(a.raw);self.assertIsNone(a.record);self.assertIn('없습니다',a.notice.get())
        self.assertEqual(len(a.canvas.find_withtag('catalog_slot')),12)
    def test_stale_saved_result_cannot_replace_new_lot(self):
        a=self.app;_,record=save_sample(self.root,'A');scene=load_saved_scene(self.root,record['image_id'])
        token=(a.generation,str(a.root),'A');a.lot.set('B');a.change_lot()
        a.accept('load_latest',token,{'scene':scene,'warnings':[]},None)
        self.assertEqual(a.lot.get(),'B');self.assertIsNone(a.scene);self.assertEqual(len(a.cards),16)
    def test_loading_saved_scene_refreshes_current_lot_scope(self):
        a=self.app;_,record=save_sample(self.root,'B');scene=load_saved_scene(self.root,record['image_id']);a.scope.set('현재 주차장')
        with patch.object(a,'refresh_stats') as scan:a.accept('load',a.generation,scene,None)
        scan.assert_called_once();self.assertEqual(a.active_lot,'B')


class DemoSwitch(unittest.TestCase):
    def test_a_b_a_switch_and_real_capture_separation(self):
        temp=tempfile.TemporaryDirectory();window=tk.Tk();window.withdraw();cfg,_,_=fixture();app=None
        try:
            app=DemoDashboard(window,cfg,Path(temp.name),PROJECT)
            for lot,count in [('A',12),('B',16),('A',12)]:
                if app.lot.get()!=lot:app.lot.set(lot);app.change_lot()
                self.assertEqual(len(app.roi['spaces']),count);self.assertEqual(len(app.cards),count);self.assertEqual(app.scene[0].source_kind,'synthetic')
                self.assertIn(f'현재 {lot} {count}칸',window.title())
            for action in (app.connect,app.start_automatic,app.choose_root):
                with self.assertRaises(ValueError):action()
            app.send_enabled.set(True);app.toggle_send();self.assertFalse(app.send_enabled.get());self.assertIsNone(app.hub)
        finally:
            if app:app.dirty=False;app.close();window.mainloop()
            else:window.destroy()
            app=None;window=None;gc.collect();temp.cleanup()


if __name__=='__main__':unittest.main()
