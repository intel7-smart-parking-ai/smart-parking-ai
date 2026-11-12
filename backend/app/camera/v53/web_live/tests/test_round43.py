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


    def test_slow_samples_dropped_and_resync_is_frequent(self):
        class Resp(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self, *a):return False
        calls = {'n': 0}

        def opener(url, timeout):
            calls['n'] += 1
            if calls['n'] in (2, 4):  # a 1 s TCP retry would put the midpoint 0.5 s off
                import time as _t; _t.sleep(.3)
            now = datetime.now(timezone.utc).timestamp() - .5
            return Resp(json.dumps({'timestamp': datetime.fromtimestamp(now, timezone.utc).isoformat()}).encode())
        value = clock.measure('http://x', opener=opener)
        self.assertAlmostEqual(value, -.5, delta=.05)
        self.assertLessEqual(clock.RESYNC_SECONDS, 15)

    def test_sync_switch(self):
        self.assertTrue(clock.sync_enabled({}))
        self.assertFalse(clock.sync_enabled({'V53_CLOCK_SYNC': '0'}))


    def test_sync_applies_median_of_recent_samples(self):
        # Server sawtooth: slow drift then a step back (11/11 15:25).
        samples = iter([-0.3, -0.4, -0.5, -0.6, -0.1, -0.2, -0.3])
        applied = []
        stop = threading.Event()
        def fake_measure(base):
            try:return next(samples)
            except StopIteration:
                stop.set();return None
        real_set = clock.set_offset
        with mock.patch.object(clock, 'measure', fake_measure),              mock.patch.object(clock, 'set_offset', side_effect=lambda v: applied.append(round(v, 3)) or real_set(v)):
            clock.start_sync('http://x', interval=0, stop_event=stop).join(2)
        self.assertEqual(applied[-1], -0.3)          # median of all seven, not the last sample
        self.assertEqual(applied[0], -0.3)
        self.assertTrue(all(-0.6 <= v <= -0.1 for v in applied))


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



class SmallShiftAdoption(unittest.TestCase):
    """11/11 15:47: moving cars nudged the Busan board 2-3 px and its approval never came back."""

    def model(self):
        outline = np.float32([[100, 100], [900, 100], [900, 700], [100, 700]])
        image = np.zeros((1080, 1920, 3), np.uint8)
        m = ParkingPipeline.__new__(ParkingPipeline)
        m.confirmed = {}; m.fixed_views = {}; m.start_views = {}
        m.interrupted_views = {'1': ((5, 'B', outline, image.shape), {'ref': 0}, 10**12, None)}
        m.registry = mock.Mock()
        m.registry.fixed_view.return_value = {'ref': 'new'}
        return m, image, outline

    def run_frames(self, m, image, outlines, epoch=5):
        out = []
        for o in outlines:
            paused = m.interrupted_views.get('1')
            out.append(bool(paused) and m._adopt_small_shift('1', epoch, 'B', o, {'outline': o}, image, paused))
        return out

    def test_small_steady_shift_adopted_after_five_frames(self):
        m, image, outline = self.model()
        shifted = outline + np.float32([[3, 20]] * 4)          # 20 px at the corners: past same_pose, under 31 px
        self.assertEqual(self.run_frames(m, image, [shifted] * 5), [False] * 4 + [True])
        self.assertEqual(m.confirmed['1'][:2], (5, 'B'))
        self.assertTrue(np.allclose(m.confirmed['1'][2], shifted))
        self.assertEqual(m.fixed_views['1'], (5, {'ref': 'new'}))
        m.registry.save_start_view.assert_not_called()   # the saved view stays person-approved
        self.assertNotIn('1', m.interrupted_views)

    def test_repeated_small_shifts_do_not_add_up(self):
        # Review 11/11: each adoption must be measured from the pose a person approved.
        m, image, outline = self.model()
        m.human_poses = {'1': ('B', outline.copy())}
        first = outline + np.float32([[3, 20]] * 4)       # 20.2 px from the approved pose
        self.assertEqual(self.run_frames(m, image, [first] * 5)[-1], True)
        # Later the board slips again by the same amount from the adopted pose.
        m.interrupted_views['1'] = ((5, 'B', m.confirmed['1'][2], image.shape), {'ref': 'new'}, 10**12, None)
        m.confirmed.pop('1')
        second = outline + np.float32([[6, 40]] * 4)      # 20 px from the adoption, 40 px from the approval
        self.assertEqual(self.run_frames(m, image, [second] * 6), [False] * 6)
        self.assertNotIn('1', m.confirmed)

    def test_big_or_unsteady_shift_not_adopted(self):
        m, image, outline = self.model()
        self.assertEqual(self.run_frames(m, image, [outline + 40] * 6), [False] * 6)
        self.assertNotIn('1', m.confirmed)
        m, image, outline = self.model()
        wobble = [outline + d for d in (0, 15, 0, 15, 0, 15)]   # within 31 px but alternating ~21 px apart
        self.assertEqual(self.run_frames(m, image, wobble), [False] * 6)
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



class ReviewFixes1112(unittest.TestCase):
    """11/12 review: held events after re-approval, and one-camera starts overwriting saved poses."""

    def test_unapproved_frame_clears_held_state(self):
        from web_live.live import LiveSession
        s = LiveSession.__new__(LiveSession)
        scope = ('1', 'B', 3, 1, 1)
        s.held_state = {(scope, 'A-01'): {'event': {'event_type': 'vehicle_observed'}, 'held': 0, 'seq': 5},
                        (('2', 'A', 3, 1, 1), 'A-01'): {'event': {}, 'held': 0, 'seq': 5}}
        out = s._held_heartbeats(scope, {'roi_confirmed': False, 'spaces': []}, {'sequence': 6}, [])
        self.assertEqual(out, [])
        self.assertEqual([k[0][0] for k in s.held_state], ['2'])   # only the other camera keeps its state

    def test_one_camera_approval_does_not_save_start_view(self):
        m = ParkingPipeline.__new__(ParkingPipeline)
        import threading
        m.lock = threading.RLock()
        m.confirmed = {}; m.fixed_views = {}; m.start_views = {}; m.interrupted_views = {}
        m.baselines = {}; m.fire_watch = None; m.registry = mock.Mock()
        m.registry.fixed_view.return_value = {'ref': 1}
        outline = np.float32([[0, 0], [10, 0], [10, 10], [0, 10]])
        result = {'registration_ok': True, '_outline': outline, 'stream_epoch': 1, 'lot_id': 'B',
                  'frame_sequence': 1, '_pose': {'lot_id': 'B', 'outline': outline}}
        frame = np.zeros((20, 20, 3), np.uint8)
        m.approve_roi('1', result, frame, save_start=False)
        m.registry.save_start_view.assert_not_called()
        m.approve_roi('1', result, frame)
        m.registry.save_start_view.assert_called_once()


if __name__ == '__main__':
    unittest.main()
