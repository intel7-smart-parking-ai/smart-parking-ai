import unittest
from unittest.mock import patch

import cv2
import numpy as np

from web_live.camera_hub import CameraHub
from web_live.live import LiveSession
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.live = LiveSession(Pipeline(), transport_factory=Transport)
        self.live.running = True
        self.live.hub = Hub([])
        self.addCleanup(self.live.close)

    def test_jpeg_preserves_aspect_and_equal_sequence_has_no_body(self):
        hub = self.live.hub
        hub.feed('1', 3, 47)
        hub.packets['1']['image'] = np.zeros((1080, 1920, 3), np.uint8)
        status, key, body = self.live.preview('1')
        self.assertEqual((status, key), ('frame', (3, 47)))
        decoded = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, (540, 960, 3))
        self.assertEqual(self.live.preview('1', *key), ('unchanged', key, None))
        hub.feed('1', 4, 47)
        self.assertEqual(self.live.preview('1', *key)[:2], ('frame', (4, 47)))

    def test_no_stale_preview_after_reconnect_during_encoding(self):
        hub = self.live.hub
        hub.feed()
        encode = cv2.imencode
        def reconnect(*args):
            hub.feed(epoch=2)
            return encode(*args)
        with patch('web_live.live.cv2.imencode', side_effect=reconnect):
            self.assertEqual(self.live.preview('1'), ('offline', None, None))
        hub.packets['1']['received'] -= 2
        self.assertEqual(self.live.preview('1'), ('offline', None, None))

    def test_snapshot_drops_wrong_epoch_before_next_analysis(self):
        hub = self.live.hub
        hub.feed()
        packet = hub.latest('1')
        result = self.live.pipeline.analyze(packet['image'], camera='1', epoch=1,
            sequence=1, observed_at=packet['observed_at'])
        result['annotated_image'] = 'image'
        self.live.results['1'] = result
        self.live.packets['1'] = packet
        self.assertNotIn('annotated_image', self.live.snapshot(False)['results']['1'])
        self.assertIn('annotated_image', self.live.snapshot()['results']['1'])
        hub.feed(epoch=2)
        self.assertEqual(self.live.snapshot()['results'], {})


class SensorSequenceTests(unittest.TestCase):
    def receive(self, numbers):
        hub = CameraHub([{}])
        now = [10.]
        feed = iter(numbers)
        class FakeSDK:
            def try_wait_for_frames(self, timeout):
                self.number = next(feed)
                now[0] += .35
                if self.number == 'stop':
                    hub.stop_event.set()
                    return False, None
                return self.number is not None, self
            def get_color_frame(self): return self
            def get_frame_number(self): return self.number
            def get_data(self): return np.zeros((2, 2, 3), np.uint8)
        with patch('web_live.camera_hub.time.monotonic', side_effect=lambda: now[0]):
            hub._receive('1', 5, FakeSDK())
        return hub.frames['1']

    def test_brief_waits_and_duplicate_counter_keep_same_connection(self):
        frame = self.receive([25, None, 26, 26, 27, 'stop'])
        self.assertEqual((frame['epoch'],frame['sequence']), (5,27))

    def test_expired_frame_then_new_rgb_keeps_device_open_but_ends_review(self):
        frame = self.receive([25, None, 25, None, 26, 'stop'])
        self.assertEqual((frame['epoch'],frame['sequence']), (6,26))

    def test_frozen_disconnected_or_reset_sensor_requests_reconnect(self):
        for sequence in ([25]*10,[1]+[None]*10,[25,2]):
            with self.subTest(sequence=sequence), self.assertRaises(RuntimeError):
                self.receive(sequence)
