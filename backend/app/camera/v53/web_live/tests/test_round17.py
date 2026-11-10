from datetime import datetime, timezone
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from web_live.camera_hub import CameraHub
from web_live.live import LiveSession
from web_live.number_reader import NumberReader
from web_live.pipeline import ParkingPipeline
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport


class CameraContinuityTests(unittest.TestCase):
    def test_capture_gap_changes_epoch_even_if_inference_misses_the_wait(self):
        hub = CameraHub([{}, {}])
        hub.epochs = {"1": 4, "2": 3}
        sdk = Mock()
        color = sdk.get_color_frame.return_value
        color.get_frame_number.side_effect = [19, 20, 21]
        color.get_data.return_value = np.zeros((4,4,3), np.uint8)
        def stop(_):
            hub.stop_event.set()
            return False, None
        readings = iter([(True, sdk)] * 3)
        sdk.try_wait_for_frames.side_effect = lambda t: next(readings, None) or stop(t)
        with patch("web_live.camera_hub.time.monotonic", side_effect=[10.,10.1,11.45,11.52,11.55]):
            hub._receive("1", 4, sdk)
        self.assertEqual((hub.frames["1"]["sequence"], hub.frames["1"]["epoch"]), (21, 5))
        self.assertEqual(hub.epochs, {"1": 5, "2": 3})

    def test_preview_requests_do_not_keep_unattended_delivery_alive(self):
        live = LiveSession(Pipeline(), transport_factory=Transport)
        self.addCleanup(live.close)
        live.hub = Hub([])
        live.running = live.sending = True
        live.hub.feed()
        live.last_client = time.monotonic() - 31
        self.assertEqual(live.preview("1")[0], "frame")
        self.assertGreater(time.monotonic() - live.last_client, 30)
        live._infer_frames()
        self.assertTrue(live.watchdog_stopped)
        self.assertFalse(live.sending)


class IdentityContinuityTests(unittest.TestCase):
    def test_empty_frame_requires_fresh_ocr_but_preserves_other_camera(self):
        reader = NumberReader()
        reader.worker = Mock()
        frame = np.full((144,144,3), 128, np.uint8)
        poly = np.float32([[0,0],[140,0],[140,140],[0,140]])
        slot = "A-01"
        pose = dict(lot_id="A", outline=poly, matrix=np.eye(3), inliers=30,
                    residual_px=.5, line_support=.9, polygons={slot:poly})
        p = ParkingPipeline.__new__(ParkingPipeline)
        p.registry = SimpleNamespace(locate=Mock(return_value=pose),boards={"A":{"spaces":[{"space_id":slot}]}})
        p.lock = threading.RLock()
        p.search_order = {}; p.baselines = {}; p.confirmed = {"1":(1,"A",poly)}
        p.numbers = reader
        sticker = dict(bbox_xyxy=[35,35,80,80],confidence=.98,sticker_color="green",vehicle_type_from_sticker="ev")
        p.detector = Mock()
        p.occupancy = Mock()
        p.number_model = SimpleNamespace(scores=lambda _: {},labels=[])
        def analyze(seq, occupied):
            p.detector.infer_bgr.return_value = dict(inference_ms=0.,detections=[dict(sticker)] if occupied else [])
            p.occupancy.scores.return_value = dict(occupied=.7,empty=.2) if occupied else dict(empty=.7,occupied=.2)
            with patch("web_live.pipeline.crop_quality",return_value=""), patch("web_live.pipeline.annotate",return_value=""):
                return p.analyze(frame,camera="1",epoch=1,sequence=seq,observed_at=datetime.now(timezone.utc).isoformat())["spaces"][0]
        first = analyze(1, True)
        self.assertIsNone(first["car_id"])
        key,crop,sig,generation,_ = reader.jobs.get_nowait()
        # One earlier OCR completion is cached and another is still being processed.
        peer = ("2",1,"B",slot,"green")
        value = (time.monotonic(),sig,dict(number="23",confidence=.95,independent_reads=2))
        reader.cache.update({key:value,peer:value})
        self.assertEqual(analyze(2, True)["car_id"], "23")
        analyze(3, False)
        self.assertEqual(analyze(4, True)["car_id"], None)
        self.assertIn(peer, reader.cache)
        self.assertNotEqual(generation, reader.pending[key])
        self.assertFalse(reader._complete(key,sig,generation,dict(number="23",confidence=.95),"1:1"))
        self.assertTrue(reader._complete(key,sig,reader.pending[key],dict(number="23",confidence=.95),"1:4"))
        r = analyze(5, True)
        self.assertIsNone(r["car_id"])
        self.assertEqual(r["ocr_independent_reads"], 1)


class LauncherTests(unittest.TestCase):
    def test_owned_listener_opens_its_port_once(self):
        from launch_web import open_when_ready
        server = SimpleNamespace(started=False, should_exit=False, config=SimpleNamespace(port=8123))
        finished = threading.Event()
        with patch("webbrowser.open") as opened:
            thread = threading.Thread(target=open_when_ready, args=(server, finished))
            thread.start()
            try:
                opened.assert_not_called()
                server.started = True
                thread.join(2)
                opened.assert_called_once_with("http://127.0.0.1:8123/")
            finally:
                finished.set()
                thread.join(2)

    def test_occupied_port_does_not_open_another_v53(self):
        import asyncio
        from contextlib import asynccontextmanager
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import runpy
        from fastapi import FastAPI

        class OldUI(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"product_version":"V53","build_revision":16}')
            def log_message(self, *args):pass

        @asynccontextmanager
        async def lifespan(app):
            await asyncio.sleep(.3)
            yield

        with ThreadingHTTPServer(("127.0.0.1",0), OldUI) as old:
            runner = threading.Thread(target=old.serve_forever, daemon=True)
            runner.start()
            try:
                with (patch("web_live.server.app", FastAPI(lifespan=lifespan)),
                      patch("webbrowser.open") as opened,
                      patch("sys.argv", ["launch_web.py","--port",str(old.server_port)])):
                    with self.assertRaises(SystemExit) as failure:
                        runpy.run_module("launch_web",run_name="__main__")
                    self.assertNotEqual(failure.exception.code, 0)
                    opened.assert_not_called()
            finally:
                old.shutdown()
                runner.join(2)
