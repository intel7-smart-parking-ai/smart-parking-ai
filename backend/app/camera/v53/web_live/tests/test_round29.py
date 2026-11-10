import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from PIL import ImageDraw
from web_live.collection import FrameCollection
from web_live.live import LiveSession
from web_live.pipeline import annotate
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport


class ChangedBayTests(unittest.TestCase):
    def setUp(self):
        self.live = LiveSession(Pipeline(), transport_factory=Transport)
        self.addCleanup(self.live.close)
        self.live.hub = Hub([])
        self.live.running = self.live.sending = True
        self.sent = {}

    def frame(self, sequence, occupied=(), held=()):
        result, packet = fixture('A')
        packet.update(sequence=sequence, received=time.monotonic())
        result['frame_sequence'] = sequence
        for index, row in enumerate(result['spaces']):
            if index in held:
                row.update(observation='unknown', sendable=False)
            elif index in occupied:
                row.update(observation='occupied', car_id='23', vehicle_type='ev')
        self.live.packets['1'] = self.live.hub.packets['1'] = packet
        self.live.results['1'] = result

    def offer(self):
        self.live.transport.offers.clear()
        self.live._offer_current(self.sent)
        return [e for _, events, _ in self.live.transport.offers for e in events]

    def test_moves_only_send_changed_bays_and_cannot_retry_old_positions(self):
        self.frame(1, occupied=[0])
        start = self.offer()
        self.assertEqual(len(start), 12)
        self.frame(2, occupied=[1], held=[2])
        changed = self.offer()
        self.assertEqual([(e['space_id'], e['event_type'], e['car_id']) for e in changed],
                         [('A-01', 'space_empty_observed', None), ('A-02', 'vehicle_observed', '23')])
        self.assertFalse(self.live._valid_event(start[0]))
        self.assertFalse(self.live._valid_event(start[1]))
        self.assertTrue(all(self.live._valid_event(e) for e in changed))
        self.assertEqual(self.offer(), [])
        self.frame(3, occupied=[1], held=[2])
        self.assertEqual(self.offer(), [])
        later = time.monotonic() + 1.01
        self.live.hub.packets['1']['received'] = later
        with patch('web_live.live.time.monotonic', return_value=later):
            self.assertEqual(len(self.offer()), 11)
            self.assertEqual(self.offer(), [])

    def test_fire_pixels_use_alarm_word_on_first_emission_only(self):
        captions = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            captions.append(text)
            return original(draw, xy, text, *args, **kwargs)
        frame = np.zeros((100, 250, 3), np.uint8)
        for state in ('candidate', 'confirmed', 'off'):
            captions.clear()
            light = dict(state=state, method='red_emission', space_id='B-07', bbox_xyxy=[10,30,20,40])
            with patch.object(ImageDraw.ImageDraw, 'text', record):
                annotate(frame, dict(spaces=[], detections=[], fire_lights=[light]))
            self.assertEqual(captions, [] if state=='off' else ['모의 화재 · B-07'])
        row=dict(space_id='A-01', polygon=[[1,1],[99,1],[99,80],[1,80]], sendable=True, observation='occupied')
        for fire,expected in [({'state':'candidate'},['A-01']),
                ({'state':'candidate','method':'lens_brightening','bbox_xyxy':[10,30,20,40]},['A-01','모의 화재 · A / A-01'])]:
            captions.clear()
            with patch.object(ImageDraw.ImageDraw, 'text', record):
                annotate(frame, dict(lot_id='A',spaces=[dict(row,fire_led=fire)],detections=[]))
            self.assertEqual(captions,expected)

    def test_collection_wait_returns_after_commit_with_same_image_key(self):
        with TemporaryDirectory() as folder:
            collection = FrameCollection(Path(folder)/'test.db')
            try:
                collection.begin()
                done = threading.Event()
                def wait():
                    collection.wait_for_frame({}, 2)
                    done.set()
                waiter = threading.Thread(target=wait)
                waiter.start()
                self.assertFalse(done.wait(.03))
                row,_ = fixture('A')
                row.update(frame_sequence=7, annotated_image='frame7')
                collection.offer('1', row, True)
                self.assertTrue(done.wait(1))
                waiter.join()
                saved = collection.snapshot({}, True)['channels']['1']
                self.assertEqual(saved['result']['annotated_image'], 'frame7')
                self.assertEqual(saved['saved'], 1)
            finally:
                collection.close()
