"""Native widget smoke test using synthetic frames, never opens hardware/models."""
import copy
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import numpy as np
import main
from camera_hub import FramePacket
from layouts import initialize,snapshot,save_calibration
from pipeline import read_json
from widgets import RoiEditor,ReviewWindow

class GUIContract(unittest.TestCase):
    def tearDown(self):
        # Tk finalizers must run on the UI thread, not a later writer allocation.
        import gc
        gc.collect()
    def test_camera_a_bound_to_lot_b_capture_review_close(self):
        try:window=tk.Tk()
        except tk.TclError as exc:self.skipTest(str(exc))
        window.withdraw();cfg,_=main.load_config();cfg=copy.deepcopy(cfg);cfg['camera_lots']['A']='B'
        errors=[];window.report_callback_exception=lambda *args:errors.append(args)
        image=np.zeros((100,100,3),np.uint8)
        packet=FramePacket('A','fixture',1,'2026-11-03T00:00:00Z',time.monotonic(),image)
        class Hub:
            def get_latest_frame(self,c):return packet if c=='A' else None
            def request_stop(self):pass
        with tempfile.TemporaryDirectory() as d:
            project=Path(d);initialize(project)
            with patch.object(main,'ROOT',project):
                app=main.App(window,cfg,project/'datasets')
                try:
                    dialog=RoiEditor(window,image,'B');dialog.withdraw()
                    dialog.points=[[10,10],[90,10],[90,90],[10,90]];dialog.register();dialog.finish()
                    roi=snapshot('B','A','fixture',[100,100],cfg['camera_pose']['A'],dialog.result);save_calibration(project,roi)
                    app.hub=Hub();app.poll();app.confirm('A');app.capture_once('A')
                    deadline=time.monotonic()+3
                    while app.counts['A']==0 and time.monotonic()<deadline:window.update();time.sleep(.01)
                    self.assertEqual(app.counts['A'],1)
                    paths=list((app.root/'manifests').glob('*.json'));self.assertEqual(len(paths),1)
                    record=read_json(paths[0]);self.assertEqual(record['lot_id'],'B');self.assertEqual(record['camera_id'],'A')
                    review=ReviewWindow(window,app.root,record);review.withdraw();review.number.set('12')
                    sticker=RoiEditor(review,image,'B',sticker_only=True);sticker.withdraw();sticker.points=[[20,20],[60,20],[60,60],[20,60]];sticker.finish()
                    self.assertEqual(len(sticker.result),4)
                    review.sticker_polygon=sticker.result;review.measure_color();review.color.set('green');review.occupancy.set('occupied');review.commit();review.draw();review.destroy();self.assertEqual(review._field_traces,[])
                    self.assertFalse(errors)
                finally:app.close();window.mainloop()
