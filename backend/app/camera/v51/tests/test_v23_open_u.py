"""Open entrances, rotated names, static floor text and one-shot photo collection."""
import copy,gc,tempfile,time,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch,MagicMock
import cv2,numpy as np
import tkinter as tk
from test_v19 import PROJECT
from test_v21_auto import board
from layouts import layout,validate_snapshot
from automatic_setup import prepare_auto_scene,detect_spaces
from static_markings import appearance,unchanged_regions
from vision19 import VisionEngine
from dashboard import Dashboard
from dataset19 import DatasetIndex
from collection_output import export_collection


def open_board(lot='A'):
    cfg,p,reader=board(lot);columns=6 if lot=='A' else 8
    image=np.full_like(p.image,65)
    for i,slot in enumerate(layout(lot)['spaces']):
        x=i%columns*200+8;y=i//columns*260+8
        poly=[[x+20,y],[x+180,y],[x+160,y+240],[x,y+240]] if lot=='B' else [[x,y],[x+180,y],[x+180,y+240],[x,y+240]]
        cv2.polylines(image,[np.asarray([poly[3],poly[0],poly[1],poly[2]])],False,(235,235,235),3)
        cv2.putText(image,slot['space_id'],(x+40,y+212),cv2.FONT_HERSHEY_SIMPLEX,.65,(235,235,235),2)
    return cfg,replace(p,image=image,source_kind='import'),reader


class OpenBoundaries(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def ready(self,lot='A'):
        cfg,p,reader=open_board(lot);r=prepare_auto_scene(self.root,p,lot,'test-pose',lambda:reader)
        self.assertTrue(r['ready'],r.get('errors'));return cfg,p,reader,r
    def test_twelve_rectangular_u_bays(self):
        *_,result=self.ready();self.assertEqual(len(result['roi']['spaces']),12)
        self.assertTrue(all(s.get('boundary_kind')=='open_u' for s in result['roi']['spaces']))
    def test_sixteen_slanted_u_bays(self):
        *_,result=self.ready('B');self.assertEqual(len(result['roi']['spaces']),16)
        self.assertTrue(all(s.get('boundary_kind')=='open_u' for s in result['roi']['spaces']))
    def test_missing_divider_does_not_invent_bay(self):
        cfg,p,reader=open_board();image=p.image.copy()
        # Synthetic bays have a 20px gap and two parallel strokes for one
        # separator. Remove both; a surviving stroke is still line evidence.
        cv2.rectangle(image,(182,3),(214,254),(65,65,65),-1)
        result=detect_spaces(image,'A',reader);self.assertFalse(result['ready']);self.assertIn('A-01',result['missing'])
    def test_rotated_names_return_original_coordinates(self):
        cfg,p,reader=open_board();rotated=replace(p,image=cv2.rotate(p.image,cv2.ROTATE_90_CLOCKWISE))
        class RotatedReader:
            def readtext(self,pixels,**kwargs):return reader.readtext(pixels,**kwargs) if pixels.shape[1]>pixels.shape[0] else []
        result=prepare_auto_scene(self.root,rotated,'A','test-pose',RotatedReader)
        self.assertTrue(result['ready'],result['errors']);validate_snapshot(result['roi'])
        self.assertEqual(result['roi']['image_size'],list(rotated.image.shape[1::-1]))
        self.assertTrue(all(r['view_rotation_clockwise']==270 for r in result['text_evidence']))
    def test_virtual_edge_is_not_painted_evidence(self):
        *_,r=self.ready();slot=r['roi']['spaces'][0]
        self.assertEqual(len(slot['painted_edges']),3);self.assertNotIn(slot['entrance_edge'],slot['painted_edges'])
        bad=copy.deepcopy(r['roi']);bad['spaces'][0]['entrance_edge'][0][0]+=5
        with self.assertRaises(ValueError):validate_snapshot(bad)
    def test_low_line_support_rejected(self):
        *_,r=self.ready();r['roi']['spaces'][0]['line_support'][0]=.1
        with self.assertRaises(ValueError):validate_snapshot(r['roi'])
    def test_corrupt_static_text_reference_rejected(self):
        *_,r=self.ready();r['roi']['auto_validation']['text_evidence'][0]['appearance']['sha256']='0'*64
        with self.assertRaises(ValueError):validate_snapshot(r['roi'])
    def test_unchanged_print_is_excluded_but_new_sticker_is_not(self):
        cfg,p,reader,r=self.ready();roi=r['roi'];regions=unchanged_regions(p.image,roi);self.assertIn('A-01',regions)
        image=p.image.copy();cv2.rectangle(image,(42,191),(117,239),(235,235,235),-1)
        cv2.putText(image,'23',(48,229),cv2.FONT_HERSHEY_SIMPLEX,1.,(0,0,0),2)
        self.assertNotIn('A-01',unchanged_regions(image,roi))
        pred=VisionEngine().run(replace(p,image=image),roi,cfg,mode='sticker')
        row=next(x for x in pred['spaces'] if x['space_id']=='A-01')
        self.assertEqual(row['occupancy'],'occupied');self.assertEqual(row['color'],'white')


class PhotoFlow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.reader=open_board()
        self.window=tk.Tk();self.window.withdraw();self.app=Dashboard(self.window,self.cfg,self.root,PROJECT)
    def tearDown(self):
        self.app.dirty=False;self.app.reviewing=False;self.app.close();self.window.mainloop()
        self.app=None;self.window=None;gc.collect();self.temp.cleanup()
    def import_photo(self):
        self.app.clear_context();self.app.input_mode='image';self.app.raw=self.p;self.app.cfg['capture_source']={'kind':'import'};return True
    def test_photo_cancel_does_not_start_registration(self):
        with patch.object(self.app,'import_image',return_value=False),patch.object(self.app,'submit') as submit:self.app.collect_photo()
        submit.assert_not_called()
    def test_photo_failure_does_not_enter_camera_retry_loop(self):
        a=self.app;a.input_mode='image';a.auto_run=True;a.auto.set(True)
        a.accept('calibrate',(a.generation,self.p,'A'),{'ready':False,'expected_count':12,'detected_count':0,'missing':['A-01']},None)
        self.assertFalse(a.auto_run or a.live or a.auto.get());self.assertEqual(a.auto_phase,'off')
    def test_photo_error_stops_automatic_state(self):
        a=self.app;a.input_mode='image';a.auto_run=True;a.auto.set(True);a.accept('calibrate',(a.generation,self.p,'A'),None,'reader unavailable')
        self.assertFalse(a.auto_run or a.auto.get())
    def test_photo_ready_does_not_consume_connected_camera_frame(self):
        a=self.app;r=prepare_auto_scene(self.root,self.p,'A','test-pose',lambda:self.reader)
        a.input_mode='image';a.auto_run=True;a.hub=MagicMock();a.hub.is_stopped.return_value=True
        with patch.object(a,'analyze_once') as analyze:a.accept('calibrate',(a.generation,self.p,'A'),r,None)
        a.hub.get_latest_frame.assert_not_called();analyze.assert_called_once();self.assertFalse(a.live)
    def test_one_photo_registers_analyzes_saves_and_exports(self):
        a=self.app;reader=self.reader;packet=self.p
        class CombinedReader:
            def readtext(self,pixels,**kwargs):return reader.readtext(pixels,**kwargs) if pixels.shape[1]>=packet.image.shape[1] else []
        a.identity_worker.engine.reader=CombinedReader()
        with patch.object(a,'import_image',side_effect=self.import_photo):a.collect_photo()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            self.window.update()
            if a.scan_result and a.scan_result['counts']['collection_pairs']==12:break
            time.sleep(.02)
        self.assertEqual(a.scan_result['counts']['collection_pairs'],12,a.notice.get())
        self.assertEqual(a.record['source']['kind'],'import');self.assertIsNone(a.hub)
        self.assertEqual(export_collection(self.root)[1]['counts']['space_pairs'],12)


if __name__=='__main__':unittest.main()
