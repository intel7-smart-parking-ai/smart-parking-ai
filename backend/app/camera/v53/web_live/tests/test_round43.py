"""11/11 operator-run start: clock offset (422 at >0.5 s ahead), saved approval and auto send."""
import io
import json
import os
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

import numpy as np

from web_live import clock
from web_live.pipeline import ParkingPipeline


class ClockOffset(unittest.TestCase):
    def tearDown(self):
        clock.set_offset(0)

    def test_offset_applied_within_limit_only(self):
        self.assertEqual(clock.set_offset(-0.51), -0.51)
        self.assertAlmostEqual((clock.utcnow() - datetime.now(timezone.utc)).total_seconds(), -0.51, places=1)
        for bad in ('abc', 'nan', 6, -9):
            self.assertEqual(clock.set_offset(bad), 0.0)
        self.assertEqual(clock.offset(), 0.0)
        self.assertEqual(clock.from_env({'V53_CLOCK_OFFSET_SECONDS': '0.250'}), 0.25)
        self.assertIsNone(clock.from_env({}))

    def test_measure_median_from_health(self):
        stamps = iter([1.0, 1.1, 0.9, 5.0, 1.05])

        class Resp(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self, *a):return False

        def opener(url, timeout):
            self.assertTrue(url.endswith('/api/v1/health'))
            now = datetime.now(timezone.utc).timestamp() + next(stamps)
            return Resp(json.dumps({'timestamp': datetime.fromtimestamp(now, timezone.utc).isoformat()}).encode())
        value = clock.measure('http://x/', opener=opener)
        self.assertAlmostEqual(value, 1.05, delta=.05)
        self.assertIsNone(clock.measure('http://x', opener=lambda *a, **k: (_ for _ in ()).throw(OSError())))


class SavedApproval(unittest.TestCase):
    def model(self):
        outline = np.float32([[100, 100], [900, 100], [900, 700], [100, 700]])
        ref = {'shape': (1080, 1920), 'pose': {'lot_id': 'B', 'outline': outline}}
        m = ParkingPipeline.__new__(ParkingPipeline)
        m.lock = threading.RLock()
        m.confirmed = {}; m.fixed_views = {}; m.interrupted_views = {}
        m.registry = mock.Mock()
        m.registry.load_start_view.side_effect = lambda c: ref if c == '1' else None
        return m, ref, outline

    def test_saved_pose_restores_after_two_matching_frames(self):
        m, ref, outline = self.model()
        self.assertEqual(m.arm_saved_approvals(), ['1'])
        self.assertIs(m.approved_views['1'], ref)
        image = np.zeros((1080, 1920, 3), np.uint8)
        same = {'lot_id': 'B', 'outline': outline.copy()}
        m.registry.verify_fixed_view.side_effect = [same, same]
        m._recheck_interrupted(image, '1', 1, 10, 'auto', True)
        self.assertNotIn('1', m.confirmed)
        m._recheck_interrupted(image, '1', 1, 11, 'auto', True)
        self.assertEqual(m.confirmed['1'][:2], (1, 'B'))

    def test_moved_board_or_existing_approval_not_armed_or_restored(self):
        m, ref, outline = self.model()
        m.confirmed['1'] = ('x',)
        self.assertEqual(m.arm_saved_approvals(), [])
        m, ref, outline = self.model()
        m.arm_saved_approvals()
        image = np.zeros((1080, 1920, 3), np.uint8)
        m.registry.verify_fixed_view.side_effect = [{'lot_id': 'B', 'outline': outline + 30}] * 3
        for seq in (10, 11, 12):
            m._recheck_interrupted(image, '1', 1, seq, 'auto', True)
        self.assertNotIn('1', m.confirmed)


class AutoSend(unittest.TestCase):
    def live(self, sending=False, stopped=None):
        from web_live.live import LiveSession
        s = LiveSession.__new__(LiveSession)
        s._approved = {}; s.approval_lost = {}; s.sending = sending; s.send_stopped = stopped
        return s

    def test_first_approval_requests_delivery_when_opted_in(self):
        with mock.patch.dict(os.environ, {'V53_AUTO_SEND': '1'}):
            s = self.live()
            s._track_approval('1', {'roi_confirmed': True, 'lot_id': 'B'})
            self.assertTrue(s.__dict__.get('_auto_send_due'))
            s = self.live(stopped={'reason': 'operator_turned_off'})
            s._track_approval('1', {'roi_confirmed': True})
            self.assertFalse(s.__dict__.get('_auto_send_due'))
            s = self.live(sending=True)
            s._track_approval('1', {'roi_confirmed': True})
            self.assertFalse(s.__dict__.get('_auto_send_due'))
        with mock.patch.dict(os.environ, {'V53_AUTO_SEND': ''}):
            s = self.live()
            s._track_approval('1', {'roi_confirmed': True})
            self.assertFalse(s.__dict__.get('_auto_send_due'))


if __name__ == '__main__':
    unittest.main()
