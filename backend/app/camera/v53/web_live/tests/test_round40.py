"""11/11 A-04·05 LED: rejected-candidate diagnostics must not change detection."""
import os
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from web_live.camera_hub import apply_color_options, color_options
from web_live.glow import GlowModel, localize
from web_live.pipeline import (glow_detect, led_debug_bays, ParkingPipeline, pink_glow, led_identity,
                               led_car_glow_lights, split_filtered_lights)

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / 'models' / 'red_glow.npz'
CAPTURES = Path(os.environ.get('V53_CAPTURE_DIR', ROOT.parent / 'verification_v53' / 'round37' / 'captures'))
REASONS = {'low_score', 'small_box', 'weak_features', 'orange_body', 'paint_reflection'}


class Old:
    def detect(self, frame):
        return [{'bbox_xyxy': [0, 0, 4, 4]}]


@unittest.skipUnless(MODEL.is_file(), 'red_glow.npz not present')
class RejectionDiagnostics(unittest.TestCase):
    def setUp(self):
        self.model = GlowModel(MODEL)

    def frames(self):
        files = sorted(CAPTURES.glob('*.jpg'))[:12] if CAPTURES.is_dir() else []
        frames = [cv2.imread(str(f)) for f in files]
        frames = [f for f in frames if f is not None]
        # Synthetic red lamp on a dark board so the test runs without captures.
        board = np.full((240, 320, 3), 40, np.uint8)
        cv2.circle(board, (160, 120), 9, (40, 40, 255), -1)
        cv2.circle(board, (160, 120), 4, (230, 230, 255), -1)
        return frames + [board]

    def test_selection_unchanged_and_reasons_known(self):
        for frame in self.frames():
            lights, rejected = self.model.detect_with_rejections(frame)
            self.assertEqual(lights, self.model.detect(frame))
            self.assertLessEqual(len(rejected), 40)
            for item in rejected:
                self.assertIn(item['reason'], REASONS)
                self.assertLess(item['confidence'], 1.0)
                self.assertGreaterEqual(item['confidence'], .2)

    def test_old_model_without_rejections(self):
        self.assertEqual(glow_detect(Old(), None), ([{'bbox_xyxy': [0, 0, 4, 4]}], []))


class RejectionLog(unittest.TestCase):
    def test_bay_filter(self):
        self.assertEqual(led_debug_bays(' a-04, A-05 ,'), frozenset({'A-04', 'A-05'}))
        self.assertEqual(led_debug_bays(''), frozenset())

    def test_counts_per_minute_bays_only(self):
        p = ParkingPipeline.__new__(ParkingPipeline)
        items = [dict(space_id='A-04', location='space', reason='orange_body', confidence=.91),
                 dict(space_id='A-04', location='space', reason='orange_body', confidence=.88),
                 dict(space_id=None, location='aisle', reason='low_score', confidence=.5),
                 dict(space_id='A-05', location='space', reason='low_score', confidence=.6)]
        with mock.patch.dict(os.environ, {'V53_LED_DEBUG_BAYS': 'A-04'}):
            self.assertEqual(p._log_rejected_lights('2', items, now=0), [])
            self.assertEqual(p._log_rejected_lights('2', items, now=30), [])
            lines = p._log_rejected_lights('2', items, now=61)
        self.assertEqual(lines, ['A-04 orange_body 6회 최고 0.91'])
        with mock.patch.dict(os.environ, {'V53_LED_DEBUG_BAYS': ''}):
            lines = p._log_rejected_lights('2', items, now=130)
        self.assertEqual(lines, ['A-04 orange_body 2회 최고 0.91', 'A-05 low_score 1회 최고 0.60'])



class ColorOptions(unittest.TestCase):
    def test_unset_changes_nothing(self):
        self.assertEqual(color_options('1', {}), [])

    def test_camera_value_overrides_shared(self):
        env = {'V53_EXPOSURE': '150', 'V53_CAMERA_2_EXPOSURE': '90', 'V53_POWER_LINE': '60',
               'V53_WHITE_BALANCE': '4600'}
        self.assertEqual(color_options('1', env), [('power_line_frequency', 2), ('enable_auto_exposure', 0),
                         ('exposure', 150.0), ('enable_auto_white_balance', 0), ('white_balance', 4600.0)])
        self.assertIn(('exposure', 90.0), color_options('2', env))

    def test_invalid_values(self):
        for env in ({'V53_EXPOSURE': 'abc'}, {'V53_GAIN': '-1'}, {'V53_POWER_LINE': '55'}, {'V53_GAIN': 'nan'}):
            with self.assertRaises(ValueError):color_options('1', env)

    def test_one_refused_option_keeps_others(self):
        class Opt:
            exposure = 'exposure'; gain = 'gain'; enable_auto_exposure = 'enable_auto_exposure'
        class RS:
            option = Opt
        class Sensor:
            def __init__(self):self.set = {}
            def supports(self, option):return option != 'gain'
            def set_option(self, option, value):
                if option == 'exposure' and value > 1000:raise RuntimeError('out of range')
                self.set[option] = value
        sensor = Sensor()
        applied = apply_color_options(sensor, [('enable_auto_exposure', 0), ('exposure', 5000), ('gain', 64),
                                               ('power_line_frequency', 2)], RS)
        self.assertEqual(applied, ['enable_auto_exposure=0'])
        self.assertEqual(sensor.set, {'enable_auto_exposure': 0.0})



def bay_frame(pink=0, red_body=False):
    """100x100 bay on a dark board with a saturated white car; pink = tinted pixel count."""
    frame = np.full((140, 140, 3), 45, np.uint8)
    frame[30:110, 40:100] = 255                      # white body, clipped like the LED core
    if red_body:frame[25:115, 35:105] = (40, 40, 220)
    if pink:
        side = int(np.sqrt(pink))
        frame[50:50 + side, 55:55 + side] = (175, 170, 235)
    return frame


POLY = {'A-05': np.float32([[20, 20], [120, 20], [120, 120], [20, 120]])}


def row(car='11', obs='occupied', candidate=None):
    return dict(space_id='A-05', car_id=car, observation=obs, number_candidate=candidate)


class LedCarGlow(unittest.TestCase):
    def run_frames(self, frames, rows, led=frozenset({'11', '23', '42'})):
        memory, streaks, states = {}, {}, []
        for i, (frame, r) in enumerate(zip(frames, rows)):
            found = led_car_glow_lights(frame, [r], POLY, [], memory, streaks, ('1', 1, 'B'), float(i), led)
            states.append(found[0]['state'] if found else None)
        return states

    def test_pink_fraction_and_box(self):
        fraction, box = pink_glow(bay_frame(400), POLY['A-05'])
        self.assertAlmostEqual(fraction, 400 / 10000, places=2)
        self.assertEqual(box, [55, 50, 75, 70])
        self.assertEqual(pink_glow(bay_frame(0), POLY['A-05']), (0., None))

    def test_on_needs_three_frames_off_clears_at_once(self):
        on, off = bay_frame(400), bay_frame(0)
        states = self.run_frames([on, on, on, on, off, on], [row()] * 6)
        self.assertEqual(states, ['candidate', 'candidate', 'confirmed', 'confirmed', None, 'candidate'])

    def test_not_listed_or_red_body_never(self):
        self.assertEqual(self.run_frames([bay_frame(400)] * 4, [row('30')] * 4), [None] * 4)
        self.assertEqual(self.run_frames([bay_frame(400)] * 4, [row()] * 4, led=frozenset()), [None] * 4)
        # A red car misread as 11 covers far more than 15% of the bay.
        self.assertEqual(self.run_frames([bay_frame(red_body=True)] * 4, [row()] * 4), [None] * 4)
        self.assertEqual(self.run_frames([bay_frame(400)] * 4, [row(None, 'empty')] * 4), [None] * 4)

    def test_glare_hides_sticker_identity_held(self):
        on = bay_frame(400)
        rows = [row(), row(None), row(None), row(None, 'unknown'), row(None), row(None, candidate='42'), row(None)]
        self.assertEqual(self.run_frames([on] * 7, rows),
                         ['candidate', 'candidate', 'confirmed', None, 'candidate', None, None])

    def test_identity_needs_own_read_and_clears(self):
        memory = {}
        key = ('1', 1, 'B', 'A-05')
        self.assertIsNone(led_identity(row(None, candidate='11'), memory, key, 0, {'11'}))
        self.assertEqual(led_identity(row(), memory, key, 1, {'11'}), '11')
        self.assertEqual(led_identity(row(None), memory, key, 900, {'11'}), '11')
        self.assertEqual(led_identity(row(None, candidate='11'), memory, key, 901, {'11'}), '11')
        self.assertIsNone(led_identity(row(None, 'empty'), memory, key, 902, {'11'}))
        self.assertIsNone(led_identity(row(None), memory, key, 903, {'11'}))
        led_identity(row(), memory, key, 904, {'11'})
        self.assertIsNone(led_identity(row('30'), memory, key, 905, {'11'}))
        self.assertIsNone(led_identity(row(None), memory, key, 906, {'11'}))

    def test_existing_confirmed_light_kept(self):
        lights = [dict(space_id='A-05', state='confirmed')]
        found = led_car_glow_lights(bay_frame(400), [row()], POLY, lights, {}, {}, ('1', 1, 'B'), 0., {'11'})
        self.assertEqual(found, [])



class BoundaryLight(unittest.TestCase):
    """11/11 demo site: red car 21's glint 82.7% in A-02 went out as a lot-level mock fire."""
    rows = {'A-02': dict(space_id='A-02', car_id='21', observation='occupied'),
            'A-03': dict(space_id='A-03', car_id='24', observation='occupied'),
            'A-04': dict(space_id='A-04', car_id='42', observation='occupied'),
            'A-05': dict(space_id='A-05', car_id=None, observation='occupied'),
            'A-06': dict(space_id='A-06', car_id=None, observation='empty')}

    def light(self, *touching):
        return dict(space_id=None, location='boundary', touching=list(touching), bbox_xyxy=[565, 231, 648, 314],
                    confidence=.907, method='red_emission', bay_area_fraction=0.)

    def test_non_led_cars_only_filtered(self):
        led = frozenset({'11', '23', '42'})
        for touching, dropped in ((('A-02',), True), (('A-02', 'A-03'), True), (('A-02', 'A-04'), False),
                                  (('A-02', 'A-05'), False), (('A-02', 'A-06'), False), ((), False)):
            kept, filtered = split_filtered_lights([self.light(*touching)], self.rows, led)
            self.assertEqual(bool(filtered), dropped, touching)
            if dropped:self.assertEqual(filtered[0]['reason'], 'not_led_car_boundary')

    def test_without_list_unchanged(self):
        kept, filtered = split_filtered_lights([self.light('A-02')], self.rows, frozenset())
        self.assertEqual((len(kept), filtered), (1, []))
        aisle = dict(self.light(), location='aisle')
        self.assertEqual(len(split_filtered_lights([aisle], self.rows, frozenset({'11'}))[0]), 1)

    def test_localize_records_touching_bays(self):
        polys = {'A-02': np.float32([[0, 0], [100, 0], [100, 100], [0, 100]]),
                 'A-03': np.float32([[100, 0], [200, 0], [200, 100], [100, 100]])}
        outline = np.float32([[-50, -50], [300, -50], [300, 200], [-50, 200]])
        light = dict(bbox_xyxy=[80, 40, 110, 70])
        localize([light], outline, polys)
        self.assertEqual((light['location'], light['touching']), ('boundary', ['A-02', 'A-03']))


if __name__ == '__main__':
    unittest.main()
