"""Round 38 (11/10 classroom review): no silent delivery stops, every-frame heartbeat, visible logs."""
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from web_live.live import LiveSession
from web_live.tests import test_round29
from web_live.tests.test_lifecycle import Pipeline, Transport


class HeartbeatTests(unittest.TestCase):
    def test_steady_bays_are_offered_on_the_next_frame(self):
        case = test_round29.ChangedBayTests('test_moves_only_send_changed_bays_and_cannot_retry_old_positions')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.frame(1, occupied=[0])
        self.assertEqual(len(case.offer()), 12)
        # 1.55 fps: the next frame arrives ~0.65 s later. A 1 s timer skipped it (gap 1.29 s).
        later = time.monotonic() + .65
        case.frame(2, occupied=[0])
        case.live.hub.packets['1']['received'] = later
        with patch('web_live.live.time.monotonic', return_value=later):
            self.assertEqual(len(case.offer()), 12)
        self.assertLessEqual(LiveSession.HEARTBEAT_SECONDS, .65)

    def test_same_frame_is_never_offered_twice(self):
        case = test_round29.ChangedBayTests('test_moves_only_send_changed_bays_and_cannot_retry_old_positions')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.frame(1, occupied=[0])
        case.offer()
        later = time.monotonic() + 5
        with patch('web_live.live.time.monotonic', return_value=later):
            self.assertEqual(case.offer(), [])


class SilentStopTests(unittest.TestCase):
    def live(self):
        live = LiveSession(Pipeline(), transport_factory=Transport)
        self.addCleanup(live.close)
        return live

    def test_stopping_while_sending_records_reason_and_warns(self):
        live = self.live()
        live.running = live.sending = True
        with self.assertLogs('web_live.live', 'WARNING') as logs:
            live.stop()
        self.assertFalse(live.sending)
        self.assertEqual(live.send_stopped['reason'], 'camera_stopped')
        self.assertIn('camera_stopped', '\n'.join(logs.output))

    def test_stopping_while_not_sending_keeps_previous_reason(self):
        live = self.live()
        live.send_stopped = {'reason': 'operator_turned_off', 'at': 'x'}
        live.stop()
        self.assertEqual(live.send_stopped['reason'], 'operator_turned_off')

    def test_configuration_change_records_reason(self):
        live = self.live()
        live.sending = True
        live.configure(.85, {'1': 'auto', '2': 'auto'})
        self.assertFalse(live.sending)
        self.assertEqual(live.send_stopped['reason'], 'configuration_changed')

    def test_released_approval_is_logged_shown_and_cleared(self):
        live = self.live()
        with live.lock:
            live._track_approval('2', {'roi_confirmed': True, 'lot_id': 'A', 'registration_ok': True})
        with self.assertLogs('web_live.live', 'WARNING') as logs, live.lock:
            live._track_approval('2', {'roi_confirmed': False, 'lot_id': 'A', 'registration_ok': True})
        self.assertIn('approval released', '\n'.join(logs.output))
        self.assertEqual(live.snapshot(False)['approval_lost']['2']['lot_id'], 'A')
        with live.lock:
            live._track_approval('2', {'roi_confirmed': False, 'lot_id': 'A', 'registration_ok': True})
        self.assertEqual(len(live.approval_lost), 1)  # one entry, not one per frame
        with live.lock:
            live._track_approval('2', {'roi_confirmed': True, 'lot_id': 'A', 'registration_ok': True})
        self.assertEqual(live.snapshot(False)['approval_lost'], {})

    def test_never_approved_board_is_not_reported_as_released(self):
        live = self.live()
        with live.lock:
            live._track_approval('1', {'roi_confirmed': False, 'lot_id': 'B'})
        self.assertEqual(live.approval_lost, {})

    def test_publisher_survives_an_exception(self):
        live = self.live()
        live.sending = True
        calls = []

        def offer(sent):
            calls.append(1)
            if len(calls) == 1:
                raise KeyError('bad frame')
            live.stop_event.set()
            return None

        with patch.object(live, '_offer_current', side_effect=offer), \
                self.assertLogs('web_live.live', 'ERROR'):
            worker = threading.Thread(target=live._publish, daemon=True)
            worker.start()
            live.publication_ready.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 2)
        self.assertEqual(live.publish_error['error'], 'KeyError')

    def test_snapshot_reports_sender_state(self):
        live = self.live()
        snap = live.snapshot(False)
        self.assertIn('sender_alive', snap)
        self.assertFalse(snap['sender_alive'])
        self.assertIsNone(snap['publish_error'])


class LoggingSetupTests(unittest.TestCase):
    def test_info_lines_are_kept_in_console_and_file(self):
        import launch_web
        root = logging.getLogger()
        saved = (root.level, list(root.handlers))
        self.addCleanup(lambda: (root.handlers.clear(), root.handlers.extend(saved[1]), root.setLevel(saved[0])))
        with tempfile.TemporaryDirectory() as folder:
            handlers = launch_web.setup_logging(Path(folder) / 'logs')
            logging.getLogger('web_live.live').info('frame perf camera=1 test')
            for h in handlers:
                h.flush()
            text = (Path(folder) / 'logs' / 'v53.log').read_text(encoding='utf-8')
            for h in handlers:
                h.close()
            root.handlers.clear()
        self.assertIn('frame perf camera=1 test', text)


class LongOcclusionApprovalTests(unittest.TestCase):
    """11/10: hands placing cars hid a board 12-60 s; the same pose must still restore."""

    def test_same_pose_restores_after_a_minute_and_moved_pose_never(self):
        from unittest.mock import patch
        from web_live.pipeline import APPROVAL_RECHECK_SECONDS
        from web_live.tests.test_round36 import ShiftedSearchApprovalTests
        case = ShiftedSearchApprovalTests('test_moved_pose_never_restores')
        self.assertGreaterEqual(APPROVAL_RECHECK_SECONDS, 60)
        for pose_shift, restored in ((0, True), (40, False)):
            m, image, outline = case.model()
            now = time.monotonic()
            with patch('web_live.pipeline.time.monotonic', return_value=now):
                m._suspend_mismatched_approval('2', 7, image.shape, '2026-11-10T00:00:00+00:00')
            m.confirmed.clear(); m.fixed_views.clear()
            pose = {'lot_id': 'A', 'outline': outline + pose_shift}
            m.registry.verify_fixed_view.side_effect = [pose, pose]
            with patch('web_live.pipeline.time.monotonic', return_value=now + 60):
                m._recheck_interrupted(image, '2', 7, 11, 'A', True)
                m._recheck_interrupted(image, '2', 7, 12, 'A', True)
            self.assertEqual('2' in m.confirmed, restored, pose_shift)

    def test_past_the_window_nothing_restores(self):
        from unittest.mock import patch
        from web_live.pipeline import APPROVAL_RECHECK_SECONDS
        from web_live.tests.test_round36 import ShiftedSearchApprovalTests
        m, image, outline = ShiftedSearchApprovalTests('test_moved_pose_never_restores').model()
        now = time.monotonic()
        with patch('web_live.pipeline.time.monotonic', return_value=now):
            m._suspend_mismatched_approval('2', 7, image.shape, '2026-11-10T00:00:00+00:00')
        m.confirmed.clear(); m.fixed_views.clear()
        same = {'lot_id': 'A', 'outline': outline.copy()}
        m.registry.verify_fixed_view.side_effect = [same, same]
        with patch('web_live.pipeline.time.monotonic', return_value=now + APPROVAL_RECHECK_SECONDS + 1):
            m._recheck_interrupted(image, '2', 7, 11, 'A', True)
            m._recheck_interrupted(image, '2', 7, 12, 'A', True)
        self.assertNotIn('2', m.confirmed)

    def test_restore_is_logged(self):
        live = LiveSession(Pipeline(), transport_factory=Transport)
        self.addCleanup(live.close)
        with live.lock:
            live._track_approval('2', {'roi_confirmed': True, 'lot_id': 'A'})
            live._track_approval('2', {'roi_confirmed': False, 'lot_id': 'A'})
        with self.assertLogs('web_live.live', 'INFO') as logs, live.lock:
            live._track_approval('2', {'roi_confirmed': True, 'lot_id': 'A'})
        self.assertIn('approval restored', '\n'.join(logs.output))


class OcrRetryTests(unittest.TestCase):
    def test_ready_worker_resets_restart_budget(self):
        import inspect
        from web_live import number_reader
        source = inspect.getsource(number_reader.NumberReader._run_once)
        ready = source.index("self.status='OCR 준비 완료'")
        self.assertIn('self.restarts=0', source[ready:ready + 300])


if __name__ == '__main__':
    unittest.main()

