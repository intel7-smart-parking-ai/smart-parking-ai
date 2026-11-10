"""Round 39 (11/10 classroom): red paint on non-LED cars never becomes a mock fire, wherever parked."""
import unittest

from web_live.pipeline import (mock_fire_car_list, not_led_car, probable_red_body, repeat_lights,
                               split_filtered_lights)

LED = frozenset({'11', '23', '42'})


def row(car, observation='occupied', sticker='white'):
    return dict(space_id='B-01', car_id=car, observation=observation, sticker_color=sticker)


def light(score=.93, area=.01, bay='B-01', location='space'):
    return dict(method='red_emission', confidence=score, bay_area_fraction=area, space_id=bay,
                location=location, bbox_xyxy=[10, 10, 20, 20], state='confirmed')


class LedCarListTests(unittest.TestCase):
    def test_config_parsing(self):
        self.assertEqual(mock_fire_car_list(['11', '23', '42']), LED)
        self.assertEqual(mock_fire_car_list(None), frozenset())
        self.assertEqual(mock_fire_car_list([]), frozenset())
        for bad in ('11', [11], ['1'], ['abc'], [None]):
            with self.assertRaises(ValueError):
                mock_fire_car_list(bad)

    def test_field_false_lights_on_read_non_led_cars_are_filtered_in_any_bay(self):
        # 11/10 collection.db false runs: 33 (0.945-0.968, 0.9%), 22 orange (0.913-0.931),
        # 88 (0.850-0.864, 1.7%), 30 (0.891-0.980, 0.8%), 21 (0.856, 0.6%).
        for car, score, area, bay in (('33', .968, .009, 'B-01'), ('22', .931, 0., 'B-02'),
                                      ('88', .862, .017, 'B-02'), ('30', .98, .008, 'A-05'),
                                      ('21', .856, .006, 'B-01'), ('21', .884, .254, 'B-04')):
            kept, filtered = split_filtered_lights([light(score, area, bay)], {bay: dict(row(car), space_id=bay)}, LED)
            self.assertEqual(kept, [], (car, bay))
            self.assertEqual(filtered[0]['reason'], 'not_led_car')

    def test_led_cars_keep_their_lights_including_large_roofs(self):
        # 11/10 classroom with LEDs on: 11 at A-02 0.878-0.975, 42 at B-03 0.878-0.912 / 11.94%, 23 0.946-0.958.
        for car, score, area in (('11', .878, .1419), ('42', .878, .125), ('23', .946, .016)):
            kept, filtered = split_filtered_lights([light(score, area)], {'B-01': row(car, sticker='green')}, LED)
            self.assertEqual(len(kept), 1, car)
            self.assertEqual(filtered, [])
        # Without a list the old red-body guess still applies to a large weak box.
        self.assertTrue(probable_red_body(light(.878, .125)))

    def test_unread_number_empty_bay_and_aisle_keep_optical_rules(self):
        self.assertFalse(not_led_car(light(), row(None), LED))
        self.assertFalse(not_led_car(light(), row('33', observation='empty'), LED))
        self.assertFalse(not_led_car(light(location='aisle'), row('33'), LED))
        self.assertFalse(not_led_car(light(), row('33'), frozenset()))
        kept, _ = split_filtered_lights([light(.95, .01)], {'B-01': row(None)}, LED)
        self.assertEqual(len(kept), 1)

    def test_no_list_keeps_previous_behaviour(self):
        kept, filtered = split_filtered_lights([light(.93, .01)], {'B-01': row('33')})
        self.assertEqual(len(kept), 1)
        self.assertEqual(filtered, [])


class WeakLightRepeatTests(unittest.TestCase):
    def run_frames(self, score, area=.01, frames=3):
        previous = []
        states = []
        for _ in range(frames):
            current = [light(score, area)]
            repeat_lights(current, previous)
            states.append(current[0]['state'])
            previous = current
        return states

    def test_weak_red_emission_needs_three_frames(self):
        self.assertEqual(self.run_frames(.856), ['candidate', 'candidate', 'confirmed'])

    def test_strong_red_emission_still_confirms_on_second_frame(self):
        self.assertEqual(self.run_frames(.93), ['candidate', 'confirmed', 'confirmed'])

    def test_single_frame_paint_glint_never_confirms(self):
        current = [light(.856)]
        repeat_lights(current, [])
        later = [light(.856, bay='B-02')]  # different bay next frame: no repeat
        repeat_lights(later, current)
        self.assertEqual(later[0]['state'], 'candidate')


class MissedFrameLedTests(unittest.TestCase):
    """Busan A-02 car 23: one missed frame must not drop the mock fire for two more frames."""

    def step(self, history, seen):
        from web_live.pipeline import carry_missed_lights, repeat_lights
        current = [light(.94, .03, 'A-02')] if seen else []
        previous = history['lights'] + history['carried']
        repeat_lights(current, previous)
        history.update(lights=current, carried=carry_missed_lights(current, previous))
        return current[0]['state'] if current else None

    def test_one_missed_frame_continues_confirmed(self):
        h = {'lights': [], 'carried': []}
        self.assertEqual([self.step(h, s) for s in (1, 1, 0, 1, 1)],
                         ['candidate', 'confirmed', None, 'confirmed', 'confirmed'])

    def test_three_missed_frames_continue_four_start_over(self):
        from web_live.pipeline import LED_MISSED_FRAMES
        self.assertEqual(LED_MISSED_FRAMES, 3)
        h = {'lights': [], 'carried': []}
        self.assertEqual([self.step(h, s) for s in (1, 1, 0, 0, 0, 1)],
                         ['candidate', 'confirmed', None, None, None, 'confirmed'])
        h = {'lights': [], 'carried': []}
        self.assertEqual([self.step(h, s) for s in (1, 1, 0, 0, 0, 0, 1, 1)],
                         ['candidate', 'confirmed', None, None, None, None, 'candidate', 'confirmed'])

    def test_unseen_frames_report_nothing(self):
        h = {'lights': [], 'carried': []}
        for s in (1, 1):
            self.step(h, s)
        self.step(h, 0)
        self.assertEqual(h['lights'], [])  # nothing reported or sent while unseen
        self.assertEqual(len(h['carried']), 1)

    def test_unconfirmed_candidate_is_not_remembered(self):
        h = {'lights': [], 'carried': []}
        self.assertEqual([self.step(h, s) for s in (1, 0, 1)], ['candidate', None, 'candidate'])


class MovedCarPinTests(unittest.TestCase):
    """11/10 classroom: swapped cars (10<->49, 88->A-05, 33<->40) stayed null without an empty frame."""

    def frame(self, pins, cars, lot='A', elsewhere=None):
        from web_live.policy import apply_bay_pins
        from web_live.tests.test_contract import fixture
        from web_live.tests.test_round36 import occupied
        result, _ = fixture(lot)
        for space, car in cars.items():
            occupied(result, space, car)
        apply_bay_pins(result, pins, (), elsewhere)
        return {r['space_id']: r['car_id'] for r in result['spaces'] if r['space_id'] in cars}

    def test_swap_in_one_frame_releases_both_pins(self):
        pins = {}
        self.assertEqual(self.frame(pins, {'A-01': '10', 'A-05': '49'}), {'A-01': '10', 'A-05': '49'})
        self.assertEqual(self.frame(pins, {'A-01': '49', 'A-05': '10'}), {'A-01': '49', 'A-05': '10'})
        self.assertEqual(pins[('A', 'A-01')], '49')

    def test_pinned_car_seen_on_other_camera_releases(self):
        from web_live.policy import pin_reads
        pins = {}
        self.frame(pins, {'A-01': '40'})
        # 40 now read at the other lot's B-06 (other camera's current frame).
        self.assertEqual(self.frame(pins, {'A-01': '33'}, elsewhere={'40': {('B', 'B-06')}}), {'A-01': '33'})
        self.assertTrue(pin_reads({'lot_id': 'B', 'spaces': []}) == {})

    def test_new_car_without_old_car_seen_elsewhere_stays_null(self):
        pins = {}
        self.frame(pins, {'A-01': '40'})
        self.assertEqual(self.frame(pins, {'A-01': '33'}), {'A-01': None})
        self.assertEqual(pins[('A', 'A-01')], '40')

    def test_pin_held_read_counts_as_seen(self):
        # Three-way rotation: every bay is pin-held in the first frame after the move.
        pins = {}
        self.frame(pins, {'A-01': '10', 'A-02': '49', 'A-03': '88'})
        moved = self.frame(pins, {'A-01': '88', 'A-02': '10', 'A-03': '49'})
        self.assertEqual(moved, {'A-01': '88', 'A-02': '10', 'A-03': '49'})


class SteadyNewNumberTests(unittest.TestCase):
    """Old car unreadable elsewhere (LED glare at Busan A-04/A-06): a steady new read still wins."""

    def frame(self, pins, streaks, car, space='A-02'):
        from web_live.policy import apply_bay_pins
        from web_live.tests.test_contract import fixture
        from web_live.tests.test_round36 import occupied
        result, _ = fixture('A')
        occupied(result, space, car)
        apply_bay_pins(result, pins, (), None, streaks)
        return next(r for r in result['spaces'] if r['space_id'] == space)['car_id']

    def test_eight_steady_frames_release_the_pin(self):
        from web_live.policy import PIN_RELEASE_FRAMES
        pins, streaks = {}, {}
        self.assertEqual(self.frame(pins, streaks, '11'), '11')
        seen = [self.frame(pins, streaks, '88') for _ in range(PIN_RELEASE_FRAMES)]
        self.assertEqual(seen[:-1], [None] * (PIN_RELEASE_FRAMES - 1))
        self.assertEqual(seen[-1], '88')
        self.assertEqual(pins[('A', 'A-02')], '88')

    def test_interrupted_streak_restarts(self):
        from web_live.policy import PIN_RELEASE_FRAMES
        pins, streaks = {}, {}
        self.frame(pins, streaks, '11')
        for _ in range(PIN_RELEASE_FRAMES - 1):
            self.frame(pins, streaks, '88')
        self.frame(pins, streaks, '11')  # the pinned car read again: not a move
        self.assertIsNone(self.frame(pins, streaks, '88'))
        self.assertEqual(pins[('A', 'A-02')], '11')

    def test_without_streaks_old_behaviour(self):
        pins = {}
        self.frame(pins, None, '11')
        for _ in range(20):
            self.assertIsNone(self.frame(pins, None, '88'))


class PinnedIdentityFireTests(unittest.TestCase):
    def result(self, car, reason='', candidate=None):
        return {'lot_id': 'A', 'spaces': [dict(space_id='A-03', car_id=car, observation='occupied',
                                               number_reason=reason, number_candidate=candidate,
                                               fire_led=dict(light(.91, .15, 'A-03')))],
                'fire_lights': [light(.91, .15, 'A-03')], 'filtered_lights': []}

    def test_null_number_uses_pinned_non_led_car(self):
        from web_live.policy import drop_non_led_fire
        r = drop_non_led_fire(self.result(None), {('A', 'A-03'): '21'}, LED)
        self.assertEqual(r['fire_lights'], [])
        self.assertEqual(r['filtered_lights'][0]['reason'], 'not_led_car_pinned')
        self.assertEqual(r['spaces'][0]['fire_led'], {'state': 'unverified'})

    def test_pin_held_led_candidate_keeps_its_light(self):
        from web_live.policy import drop_non_led_fire
        r = drop_non_led_fire(self.result(None, '칸 번호 고정(21) 중 다른 번호 판독', '11'), {('A', 'A-03'): '21'}, LED)
        self.assertEqual(len(r['fire_lights']), 1)

    def test_unknown_bay_or_led_pin_or_no_list_keeps_light(self):
        from web_live.policy import drop_non_led_fire
        self.assertEqual(len(drop_non_led_fire(self.result(None), {}, LED)['fire_lights']), 1)
        self.assertEqual(len(drop_non_led_fire(self.result(None), {('A', 'A-03'): '42'}, LED)['fire_lights']), 1)
        self.assertEqual(len(drop_non_led_fire(self.result(None), {('A', 'A-03'): '21'}, frozenset())['fire_lights']), 1)


if __name__ == '__main__':
    unittest.main()
