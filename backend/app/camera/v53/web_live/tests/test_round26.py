import unittest
import numpy as np

from web_live.live import LiveSession
from web_live.policy import to_events
from web_live.pipeline import empty_similarity
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually


class DisplayVacancyTests(unittest.TestCase):
    def test_equal_luma_colored_object_and_small_bright_occlusion_hold_vacancy(self):
        floor = np.full((224,160,3),76,np.uint8)
        self.assertTrue(empty_similarity(floor+8, floor))
        for color in ((20,40,167),(155,155,155)):
            for y,x in ((60,63),(0,0),(200,136)):
                crop = floor.copy()
                crop[y:y+24,x:x+24] = color
                with self.subTest(color=color, corner=(y,x)):
                    self.assertFalse(empty_similarity(crop,floor))

    def test_display_only_observations_cannot_generate_events(self):
        board, packet = fixture()
        for presence in ('empty', 'occupied'):
            for approved in (False, True):
                with self.subTest(presence=presence, approved=approved):
                    board['roi_confirmed'] = approved
                    for row in board['spaces']:
                        row.update(observation='unknown', model_observation=presence,
                                   display_occupancy=presence, sendable=False, empty_evidence_ok=False)
                    self.assertEqual(to_events(board,packet,'camera_a'), [])

    def test_queued_vacancy_rechecks_current_floor_evidence(self):
        session = LiveSession(Pipeline(),discover_fn=lambda:[{}],hub_factory=Hub,transport_factory=Transport)
        try:
            session.start()
            session.hub.feed()
            self.assertTrue(eventually(lambda: bool(session.results)))
            with session.lock:
                session.sending = True
                row = session.results['1']['spaces'][0]
                event = dict(event_type='space_empty_observed',lot_id='A',space_id=row['space_id'],
                             frame_id='camera_a:1:1:current')
                self.assertTrue(session._valid_event(event))
                row['empty_evidence_ok'] = False
                self.assertFalse(session._valid_event(event))
        finally:
            session.close()
