"""Round 36: team rule 6 fire event name and per-bay number pinning (fewer 409s)."""
import json
import unittest

from web_live.policy import apply_bay_pins, is_vehicle_fire, to_events, wire_event
from web_live.tests.test_contract import fixture

TEAM_FIELDS = {'event_id', 'event_type', 'source', 'lot_id', 'camera_id', 'space_id', 'car_id',
               'vehicle_type', 'confidence', 'bbox_xyxy', 'observed_at', 'frame_id'}


def occupied(result, space_id, car, kind='general'):
    row = next(r for r in result['spaces'] if r['space_id'] == space_id)
    row.update(observation='occupied', car_id=car, vehicle_type=kind, empty_evidence_ok=False)
    return row


class MockFireContractTests(unittest.TestCase):
    def test_every_outgoing_event_uses_team_types_and_fields(self):
        result, packet = fixture('A')
        row = occupied(result, 'A-05', '42', 'ev')
        row['sticker_bbox'] = [20, 20, 40, 40]
        result['fire_lights'] = [dict(space_id='A-05', location='space', state='confirmed',
                                      bbox_xyxy=[40, 21, 49, 30], confidence=.94),
                                 dict(space_id=None, location='aisle', state='confirmed',
                                      bbox_xyxy=[300, 300, 310, 310], confidence=.95)]
        events = to_events(result, packet, 'camera_a')
        allowed = {'vehicle_observed', 'space_empty_observed', 'mock_fire_led_observed'}
        self.assertTrue(all(e['event_type'] in allowed for e in events))
        for event in events:
            body = json.loads(json.dumps(wire_event(event)))
            self.assertEqual(set(body), TEAM_FIELDS)
        car_fire = [e for e in events if is_vehicle_fire(e)]
        aisle = [e for e in events if e['event_type'] == 'mock_fire_led_observed' and e['space_id'] is None]
        self.assertEqual([(e['space_id'], e['car_id'], e['vehicle_type']) for e in car_fire], [('A-05', '42', 'ev')])
        self.assertEqual([(e['car_id'], is_vehicle_fire(e)) for e in aisle], [(None, False)])


class BayPinTests(unittest.TestCase):
    def setUp(self):
        self.pins = {}
        self.result, _ = fixture('A')

    def read(self, car, *, space='A-01', empty=False, sendable=True):
        result, _ = fixture('A')
        row = next(r for r in result['spaces'] if r['space_id'] == space)
        if not empty:
            occupied(result, space, car)
        row['sendable'] = sendable
        apply_bay_pins(result, self.pins)
        return row

    def test_first_number_is_kept_and_a_new_number_is_null_until_empty(self):
        self.assertEqual(self.read('33')['car_id'], '33')
        changed = self.read('31')
        self.assertIsNone(changed['car_id'])
        self.assertEqual(changed['number_candidate'], '31')
        self.assertIn('칸 번호 고정(33)', changed['number_reason'])
        self.assertEqual(self.read('33')['car_id'], '33')
        # An unreadable number stays null; the pin is never sent without a reading.
        self.assertIsNone(self.read(None)['car_id'])
        self.assertEqual(self.pins[('A', 'A-01')], '33')
        self.read(None, empty=True)
        self.assertNotIn(('A', 'A-01'), self.pins)
        self.assertEqual(self.read('31')['car_id'], '31')

    def test_lot_and_bay_together_are_the_key(self):
        self.read('33', space='A-01')
        result, _ = fixture('B')
        occupied(result, 'A-01', '31')
        apply_bay_pins(result, self.pins)
        self.assertEqual(self.pins, {('A', 'A-01'): '33', ('B', 'A-01'): '31'})

    def test_unconfirmed_or_held_evidence_does_not_pin_or_release(self):
        self.read('33', sendable=False)
        self.assertEqual(self.pins, {})
        self.read('33')
        self.read(None, empty=True, sendable=False)
        self.assertEqual(self.pins, {('A', 'A-01'): '33'})
        result, _ = fixture('A')
        occupied(result, 'A-01', '31')
        result['roi_confirmed'] = False
        apply_bay_pins(result, self.pins)
        self.assertEqual(self.pins, {('A', 'A-01'): '33'})

    def test_duplicate_number_is_not_pinned(self):
        result, _ = fixture('A')
        occupied(result, 'A-01', '33')
        apply_bay_pins(result, self.pins, {'33'})
        self.assertEqual(self.pins, {})


class UnattendedSendTests(unittest.TestCase):
    """Closing the camera screen stops external delivery unless the demo PC opts in."""

    def run_idle(self, env):
        import os, time
        from unittest.mock import patch
        from web_live.collection import FrameCollection
        from web_live.live import LiveSession
        from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually
        import tempfile
        from pathlib import Path
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        collection = FrameCollection(Path(folder.name) / 'collection.db')
        with patch.dict(os.environ, env, clear=False):
            live = LiveSession(Pipeline(), discover_fn=lambda: [{}, {}], hub_factory=Hub,
                               transport_factory=Transport, collection=collection)
            self.addCleanup(live.close)
            live.start()
            live.last_client = time.monotonic() - 35
            live.sending = True
            for c in ('1', '2'):
                live.hub.feed(c)
            eventually(lambda: collection.snapshot({}, True)['channels'].get('1', {}).get('saved') == 1)
            return live

    def test_default_stops_and_records_reason(self):
        live = self.run_idle({'V53_UNATTENDED_SEND': ''})
        self.assertFalse(live.sending)
        self.assertEqual(live.send_stopped['reason'], 'operator_screen_idle_30s')

    def test_opt_in_keeps_delivery_running(self):
        live = self.run_idle({'V53_UNATTENDED_SEND': '1'})
        self.assertTrue(live.sending)
        self.assertIsNone(live.send_stopped)


class LitCarPairingTests(unittest.TestCase):
    """A lit car's occupancy heartbeat never leaves its same-frame mock fire behind (field A-05 flicker)."""

    def test_occupancy_heartbeat_carries_same_frame_fire(self):
        import time
        from unittest.mock import patch
        from web_live.tests import test_round29
        case = test_round29.ChangedBayTests('test_moves_only_send_changed_bays_and_cannot_retry_old_positions')
        case.setUp()
        self.addCleanup(case.doCleanups)
        light = dict(state='confirmed', location='space', space_id='A-01', method='red_emission',
                     confidence=.95, bbox_xyxy=[20, 20, 30, 30])

        def lit(sequence):
            case.frame(sequence, occupied=[0])
            result = case.live.results['1']
            result['fire_lights'] = [dict(light)]
            result['spaces'][0].update(fire_led=dict(light), sticker_bbox=[10, 10, 40, 40])

        lit(1)
        first = case.offer()
        self.assertTrue(any(is_vehicle_fire(e) for e in first))
        # Desynchronise the two heartbeat timers: occupancy due, LED not yet due.
        for key, value in list(case.sent.items()):
            if key[2]:
                case.sent[key] = (value[0], value[1], time.monotonic() + 5, value[3])
        lit(2)
        later = time.monotonic() + 1.01
        case.live.hub.packets['1']['received'] = later
        with patch('web_live.live.time.monotonic', return_value=later):
            batch = case.offer()
        occupancy = [e for e in batch if e['event_type'] == 'vehicle_observed' and e['space_id'] == 'A-01']
        fire = [e for e in batch if is_vehicle_fire(e)]
        self.assertTrue(occupancy)
        self.assertEqual([(e['space_id'], e['frame_id']) for e in fire],
                         [('A-01', occupancy[0]['frame_id'])])


class ShiftedSearchApprovalTests(unittest.TestCase):
    """Field 11/10: a shifted full-search pose erased the A approval for the rest of the run."""

    def model(self):
        import numpy as np
        from unittest.mock import Mock
        from web_live.pipeline import ParkingPipeline
        image = np.zeros((1080, 1920, 3), np.uint8)
        outline = np.float32([[100, 100], [900, 100], [900, 700], [100, 700]])
        m = ParkingPipeline.__new__(ParkingPipeline)
        m.confirmed = {'2': (7, 'A', outline, image.shape)}
        m.fixed_views = {'2': (7, {'ref': True})}
        m.interrupted_views = {}
        m.baselines = {}
        m.registry = Mock()
        return m, image, outline

    def test_shifted_search_suspends_and_only_original_pose_restores(self):
        m, image, outline = self.model()
        self.assertTrue(m._suspend_mismatched_approval('2', 7, image.shape, '2026-11-10T00:00:00+00:00'))
        approval, ref, deadline, previous = m.interrupted_views['2']
        self.assertEqual((approval[1], ref, previous), ('A', {'ref': True}, None))
        m.confirmed.clear(); m.fixed_views.clear()
        same = {'lot_id': 'A', 'outline': outline.copy()}
        m.registry.verify_fixed_view.side_effect = [same, same]
        m._recheck_interrupted(image, '2', 7, 11, 'A', True)
        self.assertNotIn('2', m.confirmed)
        m._recheck_interrupted(image, '2', 7, 12, 'A', True)
        self.assertEqual(m.confirmed['2'][1], 'A')
        self.assertEqual(m.fixed_views['2'], (7, {'ref': True}))

    def test_moved_pose_never_restores(self):
        m, image, outline = self.model()
        m._suspend_mismatched_approval('2', 7, image.shape, '2026-11-10T00:00:00+00:00')
        m.confirmed.clear(); m.fixed_views.clear()
        moved = {'lot_id': 'A', 'outline': outline + 40}
        m.registry.verify_fixed_view.side_effect = [moved, moved, None, None]
        for seq in (11, 12, 13, 14):
            m._recheck_interrupted(image, '2', 7, seq, 'A', True)
        self.assertNotIn('2', m.confirmed)

    def test_no_suspension_without_live_review_on_this_connection(self):
        m, image, _ = self.model()
        self.assertFalse(m._suspend_mismatched_approval('2', 8, image.shape, '2026-11-10T00:00:00+00:00'))
        self.assertFalse(m._suspend_mismatched_approval('2', 7, image.shape, None))
        m.fixed_views.clear()
        self.assertFalse(m._suspend_mismatched_approval('2', 7, image.shape, '2026-11-10T00:00:00+00:00'))
        self.assertEqual(m.interrupted_views, {})


class RedBodyTests(unittest.TestCase):
    """Field 11/10 values: red car 33 (no LED) vs real lit roofs 42/23/11."""

    def test_red_paint_box_is_rejected_and_real_leds_kept(self):
        from web_live.pipeline import probable_red_body
        light = dict(method='red_emission')
        self.assertTrue(probable_red_body(dict(light, bay_area_fraction=.171, confidence=.850)))
        self.assertTrue(probable_red_body(dict(light, bay_area_fraction=.171, confidence=.860)))
        for frac, conf in ((.155, .947), (.117, .958), (.0142, .948), (.041, .918), (.083, .918), (.032, .894),
                           (.056, .874), (.013, .916), (.021, .97)):
            self.assertFalse(probable_red_body(dict(light, bay_area_fraction=frac, confidence=conf)), (frac, conf))
        self.assertFalse(probable_red_body(dict(method='lens_brightening', bay_area_fraction=.2, confidence=.85)))


class FilteredLightRecordTests(unittest.TestCase):
    """Review 11/10: record every excluded LED box so a rehearsal can see a filtered real LED."""

    def test_split_keeps_real_leds_and_records_excluded_values(self):
        from web_live.pipeline import split_filtered_lights
        red = dict(method='red_emission', space_id='B-01', location='space', bbox_xyxy=[1389, 171, 1470, 252],
                   confidence=.8556, bay_area_fraction=.17102)
        real = dict(method='red_emission', space_id='A-02', location='space', bbox_xyxy=[1, 2, 30, 31],
                    confidence=.874, bay_area_fraction=.056)
        glint = dict(method='red_emission', space_id='A-03', location='space', bbox_xyxy=[5, 5, 9, 9],
                     confidence=.98, bay_area_fraction=.002)
        rows = {'A-03': dict(sticker_color='white', observation='occupied')}
        kept, filtered = split_filtered_lights([red, real, glint], rows)
        self.assertEqual(kept, [real])
        self.assertEqual([(f['space_id'], f['reason'], f['confidence'], f['bay_area_fraction']) for f in filtered],
                         [('B-01', 'red_body', .856, .171), ('A-03', 'paint_glint', .98, .002)])
        self.assertEqual(filtered[0]['bbox_xyxy'], [1389, 171, 1470, 252])

    def test_log_once_per_bay_and_reason_per_minute(self):
        from web_live.pipeline import ParkingPipeline
        m = ParkingPipeline.__new__(ParkingPipeline)
        item = dict(space_id='B-01', reason='red_body', confidence=.856, bay_area_fraction=.171, bbox_xyxy=[1, 1, 2, 2])
        with self.assertLogs('web_live.pipeline', level='INFO') as logs:
            self.assertEqual(m._log_filtered_lights('2', [item], now=100.0), [('2', 'B-01', 'red_body')])
        self.assertIn('bay=B-01 reason=red_body', logs.output[0])
        self.assertEqual(m._log_filtered_lights('2', [item], now=130.0), [])
        self.assertEqual(m._log_filtered_lights('1', [item], now=130.0), [('1', 'B-01', 'red_body')])
        self.assertEqual(m._log_filtered_lights('2', [item], now=161.0), [('2', 'B-01', 'red_body')])


class FramePerfTests(unittest.TestCase):
    """11/11 delay check: per-camera timing logged once a minute, no behaviour change."""

    def test_minute_summary_and_log(self):
        from web_live.live import FramePerf
        perf = FramePerf()
        self.assertIsNone(perf.maybe_log(0.0))
        for i in range(10):
            perf.record('1', dict(total_ms=300 + i, detector_ms=120), 400 + i * 10, 1.0 + i)
            perf.record('2', dict(total_ms=440, detector_ms=None), 900, 1.5 + i)
        self.assertIsNone(perf.maybe_log(30.0))
        with self.assertLogs('web_live.live', level='INFO') as logs:
            summary = perf.maybe_log(60.0, lambda: {'recent_counts': {'accepted': 12}})
        self.assertEqual(summary['1']['frames'], 10)
        self.assertEqual(summary['1']['total_ms_median'], 305)
        self.assertEqual(summary['1']['capture_to_ready_ms_max'], 490)
        self.assertIsNone(summary['2']['detector_ms_median'])
        self.assertEqual(perf.summary, summary)
        self.assertTrue(any('frame perf camera=1 frames=10' in line for line in logs.output))
        self.assertTrue(any("delivery last 60s {'accepted': 12}" in line for line in logs.output))

    def test_live_session_exposes_summary_without_constructor_field(self):
        from web_live.live import LiveSession, FramePerf
        live = LiveSession.__new__(LiveSession)
        self.assertIsInstance(live._frame_perf(), FramePerf)
        self.assertIs(live._frame_perf(), live._frame_perf())


class RejectionCodeTests(unittest.TestCase):
    def test_exit_review_and_store_full_are_kept(self):
        from web_live.transport import KNOWN_REJECTION_CODES
        self.assertTrue({'SESSION_EXIT_REVIEW', 'EXPERIMENT_STORE_FULL', 'SESSION_ID_CONFLICT'} <= KNOWN_REJECTION_CODES)


if __name__ == '__main__':
    unittest.main()
