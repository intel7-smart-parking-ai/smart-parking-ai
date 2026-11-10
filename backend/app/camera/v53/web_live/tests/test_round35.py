import threading
import time
import unittest

import numpy as np

from web_live.pipeline import ParkingPipeline
from web_live.transport import EventTransport


class ReviewedFloorTests(unittest.TestCase):
    def approve(self, *, age=0, sequence=4, epoch=1, lot='A', shift=0, interrupted=False, renewed=False):
        pipeline = ParkingPipeline.__new__(ParkingPipeline)
        pipeline.lock = threading.RLock()
        outline = np.float32([[0, 0], [19, 0], [19, 19], [0, 19]])
        result = dict(registration_ok=True, stream_epoch=1, lot_id='A', frame_sequence=4, _outline=outline)
        pipeline.confirmed = {'1': object()} if renewed else {}
        pipeline.interrupted_views = {'1': object()} if interrupted else {}
        pipeline.fire_watch = None
        evidence = ((epoch, lot), sequence, time.monotonic()-age, outline+shift, {'A-03'})
        pipeline.floor_history = {'1': evidence, '2': evidence}
        pipeline.light_history = {'1': (epoch, lot, sequence, evidence[2], outline+shift, [{'space_id': 'A-05'}])}
        pipeline.approve_roi('1', result, np.zeros((20, 20, 3), np.uint8))
        self.assertIn('2', pipeline.floor_history)
        self.assertEqual('1' in pipeline.light_history, '1' in pipeline.floor_history)
        return pipeline.floor_history.get('1')

    def test_exact_review_frame_preserves_only_first_floor_sample(self):
        self.assertEqual(self.approve()[-1], {'A-03'})

    def test_different_stale_or_interrupted_evidence_is_discarded(self):
        for params in (dict(age=4), dict(sequence=3), dict(epoch=2), dict(lot='B'),
                       dict(shift=1), dict(interrupted=True), dict(renewed=True)):
            with self.subTest(params=params):
                self.assertIsNone(self.approve(**params))


class PairedDispatchTests(unittest.TestCase):
    def test_only_same_frame_and_connection_wait_for_recent_alarm(self):
        event = dict(event_type='vehicle_observed', frame_id='new-frame')
        alarm = dict(event_type='mock_fire_led_observed', frame_id='new-frame', _vehicle_fire=True)
        args = lambda e, generation=2: (e, None, 'localhost', 'fixture', generation)
        now = time.monotonic()
        wait = EventTransport._wait_for_frame_alarm
        self.assertTrue(wait(args(event), args(alarm), now))
        self.assertFalse(wait(args(event), args(alarm), now-.11))
        self.assertFalse(wait(args(event), args(dict(alarm, frame_id='old-frame')), now))
        self.assertFalse(wait(args(event), args(alarm, 1), now))
        # A light that is not confirmed on the car roof does not delay occupancy.
        self.assertFalse(wait(args(event), args(dict(alarm, _vehicle_fire=False)), now))
