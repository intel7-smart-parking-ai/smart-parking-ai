import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import numpy as np
from web_live.registration import BoardRegistry, expected_ids


class StartupViewTests(unittest.TestCase):
    def test_launcher_only_starts_camera_when_selected(self):
        import launch_web
        server=SimpleNamespace(started=True,should_exit=False,config=SimpleNamespace(port=8011))
        finished=Mock();finished.wait.return_value=False
        live=Mock()
        with patch.object(launch_web.app.state,'live',live,create=True),patch.object(launch_web.webbrowser,'open') as browser:
            launch_web.open_when_ready(server,finished,browser=False)
            live.start.assert_not_called()
            launch_web.open_when_ready(server,finished,browser=False,start_camera=True)
            live.start.assert_called_once_with()
            browser.assert_not_called()

    def test_saved_landmarks_require_same_board_and_current_frame(self):
        registry=BoardRegistry.__new__(BoardRegistry)
        rng=np.random.default_rng(12)
        frame=rng.integers(0,256,(320,560,3),dtype=np.uint8)
        polys={}
        for i,sid in enumerate(expected_ids('A')):
            x=70+300*(i//6);y=30+41*(i%6)
            polys[sid]=np.float32([[x,y],[x+70,y],[x+70,y+36],[x,y+36]])
        pose=dict(lot_id='A',polygons=polys,outline=np.float32([[20,10],[515,10],[515,310],[20,310]]),
                  matrix=np.eye(3),inverse=np.eye(3))
        with tempfile.TemporaryDirectory() as folder:
            registry.landmark_directory=Path(folder)
            registry.save_start_view('1',registry.fixed_view(frame,pose))
            cached=registry.load_start_view('1')
            self.assertIsNotNone(cached)
            self.assertEqual(set(cached['pose']['polygons']),set(polys))
            self.assertIsNotNone(registry.verify_fixed_view(frame,cached))
            self.assertIsNone(registry.verify_fixed_view(np.roll(frame,15,axis=1),cached))
            self.assertIsNone(registry.verify_fixed_view(np.zeros_like(frame),cached))
            self.assertIsNone(registry.load_start_view('2'))
            (Path(folder)/'start-view-1.npz').write_bytes(b'incomplete')
            self.assertIsNone(registry.load_start_view('1'))
