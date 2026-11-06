"""Two-channel regressions using isolated datasets and simulated camera inputs."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from collection_output import read_collection
from dataset19 import export_training, persist_scene
from dataset_audit import build_audit
from dual_capture import CHANNELS, PairHold, save_pair
from dual_dashboard import DualDashboard
from empty_pair import EmptyView, store_empty_pair
from pipeline import read_json
from review import rows_for_edit, save_review
from test_v34 import data


class TwoCameraIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {k: data(k, k, 100 + i * .01) for i, k in enumerate(CHANNELS)}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def approve(self, record):
        rows = rows_for_edit(record)
        rows[0].update(occupancy='vacant', review_status='approved')
        save_review(self.root, record, rows, 'test')

    def save(self):
        info = save_pair(self.root, self.results, {'A':'group-a','B':'group-b'}, 'dual', 100)
        return {k: read_json(self.root/'manifests'/(m['image_id']+'.json'))
                for k, m in info['members'].items()}

    def test_audit_excludes_damaged_peer_even_in_single_lot_report(self):
        records = self.save()
        self.approve(records['A'])
        bundle = read_collection(self.root, records['B'])
        path = self.root / next(x['path'] for x in bundle['items'] if x['kind']=='korean_label')
        original = path.read_bytes()
        path.write_bytes(b'{}')
        for lot in (None, 'A'):
            report = build_audit(self.root, lot=lot)
            self.assertEqual(report['metrics']['occupancy']['compared'], 0)
            self.assertIsNone(report['metrics']['occupancy']['accuracy_including_abstention'])
            self.assertEqual(report['lots']['A']['frames'], 0)
            self.assertTrue(report['errors'])
        path.write_bytes(original)
        report = build_audit(self.root, lot='A')
        self.assertEqual(report['metrics']['occupancy']['compared'], 1)
        self.assertEqual(report['lots']['A']['frames'], 1)

    def test_dual_training_button_does_not_mix_legacy_single_images(self):
        from unittest.mock import patch
        from test_v27 import full_fixture
        cfg, p, roi = full_fixture()
        single, _ = persist_scene(self.root, p, roi, 'old', 'legacy', cfg)
        rows = rows_for_edit(single)
        for row in rows:
            row.update(occupancy='vacant', review_status='approved')
        save_review(self.root, single, rows, 'test')
        records = self.save()
        self.approve(records['A'])
        self.assertEqual(export_training(self.root, selected_only=True)[1], 12)
        all_dual, count = export_training(self.root, paired_only=True)
        self.assertEqual(count, 1)
        self.assertEqual(read_json(Path(all_dual)/'manifest.json')['samples'][0]['카메라ID'], 'A')
        app = Mock(root=self.root)
        with patch('dual_dashboard.export_training', wraps=export_training) as export:
            DualDashboard.training(app)
            _, job = app.background.call_args.args
            folder, count = job()
        self.assertEqual(count, 0)
        self.assertTrue(read_json(Path(folder)/'manifest.json')['scope']['paired_only'])
        export.assert_called_once_with(self.root, selected_only=True, paired_only=True)

    def test_empty_reference_uses_same_receive_limit_at_storage_boundary(self):
        views = {k: EmptyView(r.packet, r.roi) for k, r in self.results.items()}
        b = views['B']
        def with_delta(delta):
            return {**views, 'B': EmptyView(replace(b.packet,received_monotonic=100+delta),b.roi)}
        with self.assertRaisesRegex(ValueError, '수신'):
            store_empty_pair(self.root, with_delta(.101))
        with self.assertRaisesRegex(ValueError, '수신'):
            store_empty_pair(self.root, with_delta(.040000001), max_skew_ms=40)
        self.assertFalse((self.root/'references').exists())
        self.assertEqual(set(store_empty_pair(self.root, with_delta(.040), max_skew_ms=40)), set(CHANNELS))

    def test_disconnect_clears_running_status_for_both_channels(self):
        app = Mock()
        app.status = {k: Mock() for k in CHANNELS}
        app.empty_references = {'A': object(), 'B': object()}
        DualDashboard.handle_extra(app, 'disconnect', None)
        for status in app.status.values():
            status.set.assert_called_once_with('연결 해제')
        self.assertFalse(app.empty_references)

    def test_reference_ids_cannot_be_mixed_in_one_measurement(self):
        configs = {k: {**r.cfg, 'empty_board':{'빈기준ID':k*32, '카메라ID':k}}
                   for k, r in self.results.items()}
        for b_config in (configs['B'], self.results['B'].cfg):
            mixed = {'A':replace(self.results['A'],cfg=configs['A']),
                     'B':replace(self.results['B'],cfg=b_config)}
            with self.assertRaisesRegex(ValueError, '빈 기준 등록 쌍'):
                save_pair(self.root, mixed, dict.fromkeys(CHANNELS, 'group'), 'dual', 100)
            self.assertFalse((self.root/'dual_measurements').exists())

    def test_one_camera_reconnect_stops_before_submitting_another_analysis(self):
        from types import SimpleNamespace
        app = Mock(spec=DualDashboard)
        app.check_empty_context.side_effect = lambda frames: DualDashboard.check_empty_context(app, frames)
        frames = {k:r.packet for k,r in self.results.items()}
        frames['B'] = replace(frames['B'], stream_epoch=frames['B'].stream_epoch + 1)
        app.extra = app.write_job = None
        app.jobs = {}
        app.specs = {k:SimpleNamespace(serial=p.serial) for k,p in frames.items()}
        app.cfg = {'camera_pose':dict.fromkeys(CHANNELS, 'fixed-v1')}
        app.profile = {'channels':{k:{'serial':p.serial,'lot':k,'view':'ceiling'} for k,p in frames.items()}}
        app.empty_references = {k:(r.roi, {'epoch':r.packet.stream_epoch}) for k,r in self.results.items()}
        app.status = {k:Mock() for k in CHANNELS}
        app.note = Mock()
        app.closing = False
        app.running = True
        app.hub = Mock()
        app.hub.get_latest_frame.side_effect = frames.get
        app.hub.statuses.return_value = {k:{'status':'RUNNING'} for k in CHANNELS}
        app.pool = Mock()
        DualDashboard.step(app)
        self.assertFalse(app.running)
        self.assertIn('카메라 2', app.note.set.call_args.args[0])
        app.pool.submit.assert_not_called()
        self.assertEqual(set(app.empty_references), set(CHANNELS))

    def test_empty_registration_reports_both_incomplete_boards(self):
        app = Mock()
        app.root = self.root
        app.cfg = {}
        app.config_from_ui.return_value = {
            'max_skew_ms':100, 'channels':{k:{'lot':k,'view':'ceiling'} for k in CHANNELS}}
        app.hub.get_latest_frame.side_effect = lambda k: self.results[k].packet
        app.engines = {k: Mock() for k in CHANNELS}
        for k, cause in (('A','1/12칸'), ('B','0/16칸')):
            app.engines[k].register_roi.side_effect = PairHold(cause)
        from unittest.mock import patch
        with patch('dual_dashboard.synchronized'):
            DualDashboard.register_empty(app)
            _, prepare = app.background.call_args.args
            with self.assertRaises(PairHold) as held:
                prepare()
        self.assertIn('1/12칸', str(held.exception))
        self.assertIn('0/16칸', str(held.exception))
        for engine in app.engines.values():
            engine.register_roi.assert_called_once()
