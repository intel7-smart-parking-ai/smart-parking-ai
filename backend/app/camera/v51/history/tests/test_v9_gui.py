"""V9 GUI regression tests: synthetic frames and local files only."""
import copy
import gc
from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import numpy as np
import main
from camera_hub import FramePacket
from layouts import initialize,snapshot
from pipeline import read_json
from widgets import RoiEditor,ReviewWindow
import test_integration as fixture

class GUIRegressions(unittest.TestCase):
    @contextmanager
    def app_context(self):
        try:window=tk.Tk()
        except tk.TclError as exc:self.skipTest(str(exc))
        window.withdraw();cfg,_=main.load_config()
        with tempfile.TemporaryDirectory() as d:
            project=Path(d);initialize(project)
            with patch.object(main,'ROOT',project):
                app=main.App(window,copy.deepcopy(cfg),project/'datasets')
                try:yield app,window,project
                finally:
                    try:
                        for child in window.winfo_children():
                            if isinstance(child,tk.Toplevel):child.destroy()
                        if not app.closing:app.close()
                        window.mainloop()
                    except tk.TclError:pass
        gc.collect()

    def test_single_review_window_and_unsaved_close_cancel(self):
        with self.app_context() as (app,window,project):
            _,record=fixture.Contracts().fixture(app.root)
            app.open_review(record);review=app.review_windows[record['image_id']];review.withdraw()
            app.open_review(record);self.assertIs(app.review_windows[record['image_id']],review)
            self.assertEqual(sum(isinstance(c,ReviewWindow) for c in window.winfo_children()),1)
            review.number.set('12')
            with patch('widgets.messagebox.askyesnocancel',return_value=None) as ask:app.close()
            ask.assert_called_once();self.assertFalse(app.closing);self.assertTrue(review.winfo_exists())
            with patch('widgets.messagebox.askyesnocancel',return_value=False):self.assertTrue(review.close())

    def test_bad_review_does_not_leave_half_created_window(self):
        with self.app_context() as (app,window,project):
            _,record=fixture.Contracts().fixture(app.root);(app.root/'reviews').mkdir()
            (app.root/'reviews'/(record['image_id']+'.json')).write_text('{',encoding='utf-8')
            before=set(window.winfo_children())
            with self.assertRaises(ValueError):app.open_review(record)
            self.assertEqual(before,set(window.winfo_children()))

    def test_bad_camera_roi_does_not_stop_second_camera(self):
        with self.app_context() as (app,window,project):
            frames={c:FramePacket(c,c,1,'2026-11-03T00:00:00Z',time.monotonic(),np.zeros((100,100,3),np.uint8)) for c in ('A','B')}
            class Hub:
                def get_latest_frame(self,c):return frames[c]
                def request_stop(self):pass
            app.hub=Hub()
            def load(project,lot,c,serial,size,pose):
                if c=='A':raise ValueError('corrupt A ROI')
                return snapshot(lot,c,serial,size,pose,[fixture.SPACE])
            with patch('main.load_calibration',side_effect=load),patch('main.logging.exception'):
                app.poll();app.confirm('B');self.assertTrue(app.submit('B'))
            self.assertIn('corrupt A ROI',app.states['A'].get());self.assertEqual(app.rois['B']['lot_id'],'B')
            deadline=time.monotonic()+3
            while app.busy['B'] and time.monotonic()<deadline:app.drain_saves();time.sleep(.01)
            self.assertEqual(app.counts['B'],1)

    def test_submit_checks_fresh_hub_frame_not_old_preview(self):
        with self.app_context() as (app,window,project):
            packet=FramePacket('A','fixture',1,'now',time.monotonic(),np.zeros((100,100,3),np.uint8))
            class Hub:
                def get_latest_frame(self,c):return None
                def request_stop(self):pass
            app.hub=Hub();app.packets['A']=packet;app.confirmed['A']=True
            app.rois['A']=snapshot('A','A','fixture',[100,100],app.cfg['camera_pose']['A'],[fixture.SPACE])
            with self.assertRaises(ValueError):app.submit('A')
            self.assertEqual(app.writer.jobs.qsize(),0)

    def test_close_waits_for_camera_and_reports_late_save_failure(self):
        with self.app_context() as (app,window,project):
            class Hub:
                done=False
                def request_stop(self):pass
                def is_stopped(self):return self.done
            hub=Hub();app.hub=hub
            app.writer.results.put(('A',None,'disk full'))
            with patch('main.messagebox.showwarning') as warning:
                app.close()
                self.assertTrue(app.closing);self.assertTrue(window.winfo_exists())
                with self.assertRaises(ValueError):app.submit('A')
                hub.done=True;window.mainloop()
            warning.assert_called_once()
            self.assertEqual(read_json(app.root/'last_save_errors.json')[0]['error'],'disk full')

    def test_roi_editor_must_finish_before_review_or_app_closes(self):
        with self.app_context() as (app,window,project):
            _,record=fixture.Contracts().fixture(app.root);app.open_review(record)
            review=app.review_windows[record['image_id']];review.withdraw()
            editor=RoiEditor(review,review.image,'A',sticker_only=True);editor.withdraw()
            with patch('widgets.messagebox.showinfo') as info:app.close()
            info.assert_called_once();self.assertFalse(app.closing);self.assertTrue(editor.winfo_exists())
            editor.destroy()

    def test_v11_approve_button_saves_100(self):
        with self.app_context() as (app,window,project):
            _,record=fixture.Contracts().fixture(app.root);app.open_review(record)
            review=app.review_windows[record['image_id']];review.withdraw()
            review.reviewer.set('tester');review.occupancy.set('occupied');review.number.set('100')
            review.approve_next()
            saved=read_json(app.root/'reviews'/(record['image_id']+'.json'))
            self.assertEqual(saved['spaces'][0]['car_id'],'100')
            self.assertEqual(saved['spaces'][0]['review_status'],'approved')
    def test_v11_invalid_approval_stays_pending(self):
        with self.app_context() as (app,window,project):
            _,record=fixture.Contracts().fixture(app.root);app.open_review(record)
            review=app.review_windows[record['image_id']];review.withdraw()
            review.reviewer.set('tester');review.number.set('101')
            with patch('widgets.messagebox.showerror') as error:review.approve_next()
            error.assert_called_once();self.assertFalse(review.approved.get())
            self.assertFalse((app.root/'reviews'/(record['image_id']+'.json')).exists())

    def test_v12_zoom_pan_click_uses_original_coordinates(self):
        from types import SimpleNamespace
        with self.app_context() as (app,window,project):
            image=np.zeros((1080,1920,3),np.uint8)
            editor=RoiEditor(window,image,'A',sticker_only=True);editor.withdraw()
            editor.zoom(4,400,250);editor.pan_start(SimpleNamespace(x=300,y=200));editor.pan_move(SimpleNamespace(x=240,y=170))
            expected=editor.screen_to_image(350,250);editor.click(SimpleNamespace(x=350,y=250))
            self.assertEqual(editor.points[0],[round(v) for v in expected])
            points=copy.deepcopy(editor.points);editor.fit_view();self.assertEqual(editor.points,points)
            editor.destroy()
    def test_v12_sticker_lines_and_mode_specific_guidance(self):
        with self.app_context() as (app,window,project):
            editor=RoiEditor(window,np.zeros((100,100,3),np.uint8),'A',sticker_only=True);editor.withdraw()
            editor.points=[[20,20],[60,20],[60,60],[20,60]];editor.draw()
            self.assertIn('선택점 4/4',editor.note.get());self.assertNotIn('A-01',editor.note.get());self.assertNotIn('12칸',editor.note.get())
            line=editor.canvas.find_withtag('selection')[0];coords=editor.canvas.coords(line)
            self.assertEqual(coords[:2],coords[-2:]);editor.finish();self.assertEqual(editor.result,[[20,20],[60,20],[60,60],[20,60]])
    def test_v12_crossed_points_rejected_with_visible_warning(self):
        with self.app_context() as (app,window,project):
            editor=RoiEditor(window,np.zeros((100,100,3),np.uint8),'A',sticker_only=True);editor.withdraw()
            editor.points=[[20,20],[60,60],[60,20],[20,60]];editor.draw()
            self.assertIn('오류',editor.note.get())
            with patch('widgets.messagebox.showerror') as error:editor.finish()
            error.assert_called_once();self.assertIsNone(editor.result);editor.destroy()
    def test_v12_slot_focus_and_zoom_bounds(self):
        with self.app_context() as (app,window,project):
            polygon=[[1000,600],[1200,600],[1200,800],[1000,800]]
            editor=RoiEditor(window,np.zeros((1080,1920,3),np.uint8),'A',sticker_only=True,focus_polygon=polygon);editor.withdraw()
            self.assertGreater(editor.sx,editor.fit_scale)
            for x,y in polygon:
                px,py=editor.image_to_screen(x,y);self.assertTrue(0<=px<=editor.vw and 0<=py<=editor.vh)
            editor.zoom(1000);self.assertLessEqual(editor.sx,8)
            editor.zoom(.00001);self.assertEqual(editor.sx,editor.fit_scale);editor.destroy()

    def test_v13_multi_result_window_opens_registered_slots(self):
        from pipeline import Analyzer
        from tkinter import ttk
        with self.app_context() as (app,window,project):
            path,record=fixture.Contracts().fixture(app.root)
            worker=Analyzer(project,app.root,fixture.CFG);worker.reader=fixture.Reader([fixture.HIT]);worker.engine={};worker.process(path)
            app.latest_record=record;app.open_summary()
            dialogs=[c for c in window.winfo_children() if isinstance(c,tk.Toplevel)]
            self.assertEqual(len(dialogs),1)
            tables=[c for c in dialogs[0].winfo_children() if isinstance(c,ttk.Treeview)]
            self.assertEqual(tables[0].get_children(),('A-01',))
            self.assertEqual(str(tables[0].item('A-01','values')[0]),'12')

    def test_v14_inspection_is_original_scale_and_read_only(self):
        with self.app_context() as (app,window,project):
            editor=RoiEditor(window,np.zeros((1080,1920,3),np.uint8),'A',inspect_only=True);editor.withdraw()
            editor.zoom(1/editor.sx);self.assertEqual(editor.sx,1)
            self.assertIn('정지 프레임',editor.note.get());editor.finish();self.assertIsNone(editor.result)
    def test_v14_camera_panel_reads_actual_and_destroys_timer(self):
        from camera_tools import CameraTools
        import queue
        with self.app_context() as (app,window,project):
            class Hub:
                def get_latest_frame(self,c):return None
                def request_quality(self,c,settings):
                    result=queue.Queue();result.put({'ok':True,'actual':{}});return result
            tools=CameraTools(window,Hub(),'A',project);tools.withdraw();tools.poll()
            self.assertIn('장치 응답 확인',tools.status.get());tools.destroy()

    def test_v15_closed_settings_cancel_pending_receipt(self):
        from camera_tools import CameraTools
        import queue
        with self.app_context() as (app,window,project):
            receipt=queue.Queue()
            class Hub:
                def get_latest_frame(self,c):return None
                def request_quality(self,c,settings):return receipt
            tool=CameraTools(window,Hub(),'A',project)
            tool.tk.call(tool.protocol('WM_DELETE_WINDOW'))
            self.assertTrue(receipt.cancelled)
    def test_v15_auto_mode_readonly_fields_sent_when_switching_manual(self):
        from camera_tools import CameraTools
        import queue
        with self.app_context() as (app,window,project):
            class Hub:
                def get_latest_frame(self,c):return None
                def request_quality(self,c,settings):return queue.Queue()
            tool=CameraTools(window,Hub(),'A',project);tool.withdraw();tool.auto_exposure.set(False)
            tool.actual={'exposure':{'read_only':True},'gain':{'read_only':True}}
            tool.entries['exposure'].set('20');tool.entries['gain'].set('2')
            with patch.object(tool,'request') as request:tool.apply(False)
            self.assertEqual(request.call_args.args[0]['exposure'],20);tool.destroy()
    def test_v15_settings_window_blocks_capture_confirmation(self):
        with self.app_context() as (app,window,project):
            dialog=tk.Toplevel(window);dialog.withdraw();app.quality_windows['A']=dialog
            with self.assertRaisesRegex(ValueError,'설정 창'):app.capture_packet('A',False)
            dialog.destroy()

if __name__=='__main__':unittest.main()





