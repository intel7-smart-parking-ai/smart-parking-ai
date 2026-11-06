"""Reproduce lifecycle and dataset integrity failures without physical devices."""
import copy
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import test_integration as fixture
import test_v14 as sensors
import test_v9_gui as gui
from camera_hub import CameraHub, CameraSpec, FramePacket
from pipeline import Analyzer, needs_analysis, read_json
from quality import apply_sensor
from review import default_review, export_reviewed, save_review


class CameraLifecycle(unittest.TestCase):
    def test_get_frame_cannot_mutate_camera_option_audit(self):
        hub = CameraHub([CameraSpec('A')])
        state = hub._states['A']
        state.status = 'RUNNING'
        state.latest = FramePacket('A', 's', 1, 'now', time.monotonic(),
                                  np.zeros((10, 10, 3), np.uint8), {'gain': {'value': 2}})
        packet = hub.get_latest_frame('A')
        packet.camera_options['gain']['value'] = 999
        self.assertEqual(hub.get_latest_frame('A').camera_options['gain']['value'], 2)

    def test_shutdown_does_not_return_last_frame(self):
        hub = CameraHub([CameraSpec('A')])
        state = hub._states['A']; state.status = 'RUNNING'
        state.latest = FramePacket('A', 's', 1, 'now', time.monotonic(), np.zeros((10, 10, 3), np.uint8))
        hub.request_stop()
        self.assertIsNone(hub.get_latest_frame('A'))

    def test_inflight_setting_hides_old_frame_until_next_read(self):
        entered = threading.Event(); release = threading.Event(); applied = threading.Event(); fresh = threading.Event()
        class Source:
            reads = 0
            def open(self): pass
            def close(self): pass
            def read(self):
                self.reads += 1
                if self.reads == 2:
                    entered.set(); release.wait(3)
                if applied.is_set(): fresh.wait(3)
                return np.zeros((10, 10, 3), np.uint8)
            def apply_quality(self, changes):
                applied.set(); fresh.wait(3); return {}
        hub = CameraHub([CameraSpec('A')], source_factory=lambda _: Source())
        hub.start()
        try:
            self.assertTrue(entered.wait(2))
            receipt = hub.request_quality('A', {'gain': 2})
            release.set(); self.assertTrue(applied.wait(2))
            self.assertIsNone(hub.get_latest_frame('A'), 'setting in flight must invalidate old frame')
            fresh.set(); self.assertTrue(receipt.get(timeout=2)['ok'])
        finally:
            release.set(); fresh.set(); hub.stop()

    def test_readback_mismatch_is_not_success(self):
        class Ignores(sensors.Sensor):
            def set_option(self, option, value): self.calls.append(option)
        with self.assertRaises(RuntimeError):
            apply_sensor(Ignores(), sensors.Quality().rs(), {'gain': 2})

    def test_reconnect_changes_frame_epoch(self):
        disconnected = threading.Event(); continue_read = threading.Event(); count = [0]
        class Source:
            reads = 0
            def __init__(self): count[0] += 1; self.connection = count[0]
            def open(self): pass
            def close(self): pass
            def read(self):
                self.reads += 1
                if self.connection == 1 and self.reads == 2:
                    disconnected.set(); continue_read.wait(3); raise RuntimeError('USB unplug')
                time.sleep(.001); return np.zeros((10, 10, 3), np.uint8)
        hub = CameraHub([CameraSpec('A')], source_factory=lambda _: Source(), retry_seconds=.001)
        hub.start()
        try:
            self.assertTrue(disconnected.wait(2)); first = hub.get_latest_frame('A')
            continue_read.set(); deadline = time.monotonic() + 2
            second = None
            while time.monotonic() < deadline:
                second = hub.get_latest_frame('A')
                if second is not None and second.stream_epoch != first.stream_epoch: break
                time.sleep(.002)
            self.assertIsNotNone(second)
            self.assertGreater(second.stream_epoch, first.stream_epoch)
        finally: continue_read.set(); hub.stop()

    def test_partial_setting_failure_invalidates_previous_confirmation_epoch(self):
        waiting = threading.Event(); resume = threading.Event()
        class Source:
            reads = 0
            def open(self): pass
            def close(self): pass
            def read(self):
                self.reads += 1
                if self.reads == 2: waiting.set(); resume.wait(3)
                time.sleep(.001); return np.zeros((10, 10, 3), np.uint8)
            def apply_quality(self, changes): raise RuntimeError('partial SDK write')
        hub = CameraHub([CameraSpec('A')], source_factory=lambda _: Source()); hub.start()
        try:
            self.assertTrue(waiting.wait(2)); before = hub.get_latest_frame('A')
            receipt = hub.request_quality('A', {'gain': 2}); resume.set()
            self.assertFalse(receipt.get(timeout=2)['ok'])
            deadline = time.monotonic() + 2; after = None
            while time.monotonic() < deadline:
                after = hub.get_latest_frame('A')
                if after is not None: break
                time.sleep(.002)
            self.assertIsNotNone(after); self.assertGreater(after.stream_epoch, before.stream_epoch)
        finally: resume.set(); hub.stop()


class DatasetIntegrity(unittest.TestCase):
    def analyzed(self, root):
        path, record = fixture.Contracts().fixture(root)
        worker = Analyzer(root, root, fixture.CFG); worker.reader = fixture.Reader([fixture.HIT]); worker.engine = {}
        worker.process(path)
        return path, read_json(path), root / 'analysis' / path.name

    def test_missing_slot_status_requires_reanalysis(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path, record, result = self.analyzed(root)
            data = read_json(result); del data['spaces'][0]['status']
            result.write_text(json.dumps(data), encoding='utf-8')
            self.assertTrue(needs_analysis(record, result_path=result))

    def test_invalid_selected_number_requires_reanalysis(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path, record, result = self.analyzed(root)
            data = read_json(result); data['spaces'][0]['number_candidate'] = '101'
            result.write_text(json.dumps(data), encoding='utf-8')
            self.assertTrue(needs_analysis(record, result_path=result))

    def test_review_extra_metadata_cannot_replace_image_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); _, record = fixture.Contracts().fixture(root)
            rows = default_review(record)
            rows[0].update(review_status='approved', occupancy='occupied', plate_text='12',
                           image_id='foreign-image', raw_path='wrong.png', batch_id='wrong-batch')
            save_review(root, record, rows, 'tester')
            target, count = export_reviewed(root)
            row = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(row['image_id'], record['image_id'])
            self.assertEqual(row['raw_path'], record['raw_path'])

    def test_incomplete_export_does_not_leave_usable_dataset_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); _, record = fixture.Contracts().fixture(root)
            rows = default_review(record); rows[0].update(review_status='approved', occupancy='vacant')
            save_review(root, record, rows, 'tester')
            with patch('multi_vehicle.group_reviewed', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): export_reviewed(root)
            self.assertEqual(list((root / 'exports').iterdir()), [], 'failed bundle must not look complete')

    def test_output_write_error_is_not_swallowed_as_bad_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); _, record = fixture.Contracts().fixture(root)
            rows = default_review(record); rows[0].update(review_status='approved', occupancy='vacant')
            save_review(root, record, rows, 'tester')
            real_open = Path.open
            class BrokenWriter:
                def __init__(self, handle): self.handle = handle
                def __enter__(self): return self
                def __exit__(self, *args): self.handle.close()
                def writelines(self, lines):
                    self.handle.write('{partial'); raise OSError('disk full during write')
            def open_file(path, *args, **kwargs):
                handle = real_open(path, *args, **kwargs)
                return BrokenWriter(handle) if path.name.endswith('.jsonl.tmp') and args[0] == 'x' else handle
            with patch.object(Path, 'open', open_file):
                with self.assertRaisesRegex(OSError, 'disk full'): export_reviewed(root)
            self.assertEqual(list((root / 'exports').iterdir()), [])

    def test_full_16_slot_frame_keeps_numbers_colors_and_identity(self):
        from layouts import layout, snapshot, COLOR_TYPES
        from pipeline import save_capture
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); image = np.zeros((400, 400, 3), np.uint8)
            spaces = layout('B')['spaces']; colors = ['white', 'green', 'blue']
            pixels = [(255,255,255), (0,255,0), (255,0,0)]; polygons = []
            numbers = [f'{n:02d}' for n in range(10,25)] + ['100']
            for index, space in enumerate(spaces):
                x, y = (index % 4)*100, (index // 4)*100
                space['polygon'] = [[x+5,y+5],[x+95,y+5],[x+95,y+95],[x+5,y+95]]
                polygon = [[x+25,y+25],[x+45,y+25],[x+45,y+45],[x+25,y+45]]; polygons.append(polygon)
                image[y+25:y+46,x+25:x+46] = pixels[index % 3]
            roi = snapshot('B','A','serial',[400,400],'test',spaces)
            packet = FramePacket('A','serial',1,'now',time.monotonic(),image)
            path = save_capture(root,packet,roi,'batch','session',fixture.CFG); record = read_json(path)
            class Reader:
                def __init__(self): self.index = 0
                def readtext(self,*args,**kwargs):
                    number = numbers[self.index]; self.index += 1
                    return [(fixture.HIT[0],number,.99)]
            analyzer = Analyzer(root,root,fixture.CFG); analyzer.reader = Reader(); analyzer.engine = {}
            analyzer.process(path); result = read_json(root/'analysis'/path.name)
            self.assertEqual([s['number_candidate'] for s in result['spaces']], numbers)
            self.assertTrue(all(s['color_candidate']=='unknown' and s['occupancy']=='unknown' for s in result['spaces']))
            rows = default_review(record)
            for i,row in enumerate(rows):
                row.update(occupancy='occupied',plate_text=numbers[i],color=colors[i%3],sticker_polygon=polygons[i],review_status='approved')
            saved = save_review(root,record,rows,'tester')
            self.assertEqual([s['color_measurement']['name'] for s in saved['spaces']],[colors[i%3] for i in range(16)])
            self.assertEqual([s['vehicle_type'] for s in saved['spaces']],[COLOR_TYPES[colors[i%3]] for i in range(16)])
            target,count = export_reviewed(root); self.assertEqual(count,16)
            frame = json.loads(target.with_suffix('.frames.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(len(frame['annotations']),16); self.assertEqual(frame['lot_id'],'B')
            self.assertEqual({r['space_id'] for r in frame['annotations']},{s['space_id'] for s in spaces})
            self.assertFalse(frame['all_objects_in_image_annotated'])


class GUIIntegrity(unittest.TestCase):
    app_context = gui.GUIRegressions.app_context

    def test_fast_reconnect_requires_fresh_roi_confirmation(self):
        from layouts import snapshot
        with self.app_context() as (app, window, project):
            packet = FramePacket('A', 's', 1, 'now', time.monotonic(), np.zeros((100, 100, 3), np.uint8))
            object.__setattr__(packet, 'stream_epoch', 1)
            class Hub:
                def get_latest_frame(self, camera): return packet
                def request_stop(self): pass
            app.hub = Hub()
            app.rois['A'] = snapshot('A', 'A', 's', [100, 100], app.cfg['camera_pose']['A'], [fixture.SPACE])
            app.confirm('A')
            object.__setattr__(packet, 'stream_epoch', 2)
            with self.assertRaises(ValueError): app.capture_packet('A')

    def test_save_camera_settings_preserves_unedited_options(self):
        from camera_tools import CameraTools
        with self.app_context() as (app, window, project):
            path = project / 'camera_quality.json'
            path.write_text(json.dumps({'defaults': {}, 'A': {'contrast': 41, 'saturation': 55, 'auto_exposure_priority': 0}}), encoding='utf-8')
            class Hub:
                def get_latest_frame(self, camera): return None
                def request_quality(self, camera, settings): return queue.Queue()
            tool = CameraTools(window, Hub(), 'A', project); tool.withdraw(); tool.pending_save = True
            from quality import inspect_sensor
            actual = inspect_sensor(sensors.Sensor(), sensors.Quality().rs())
            tool.receive({'ok': True, 'actual': actual})
            tool.destroy()
            self.assertEqual(read_json(path)['A']['contrast'], 41)
            self.assertEqual(read_json(path)['A']['saturation'], 55)

    def test_unknown_auto_mode_does_not_overwrite_saved_camera_settings(self):
        from camera_tools import CameraTools
        with self.app_context() as (app,window,project):
            path=project/'camera_quality.json';path.write_text('{"A":{"exposure":10,"gain":2}}',encoding='utf-8')
            before=path.read_bytes()
            class Hub:
                def get_latest_frame(self,c):return None
                def request_quality(self,c,settings):return queue.Queue()
            tool=CameraTools(window,Hub(),'A',project);tool.withdraw();tool.pending_save=True
            tool.receive({'ok':True,'actual':{}})
            self.assertIn('저장 실패',tool.status.get());self.assertEqual(path.read_bytes(),before);tool.destroy()


class Preflight(unittest.TestCase):
    def test_check_rejects_corrupt_quality_file_before_opening_camera(self):
        import main
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)
            for name in ('config.json','cameras.json'):
                (project/name).write_bytes((Path(main.__file__).parent/name).read_bytes())
            (project/'camera_quality.json').write_text('{"A":{"gain":"bad"}}',encoding='utf-8')
            with patch.object(main,'ROOT',project),patch('sys.argv',['main.py','--check']):
                with self.assertRaisesRegex(ValueError,'camera_quality.json'):main.main()


if __name__ == '__main__': unittest.main()
