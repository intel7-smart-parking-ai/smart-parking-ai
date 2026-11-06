"""Late server blocks and rejected occupancy must not corrupt shared state."""
from copy import deepcopy
from datetime import timedelta
import threading
import unittest
from unittest.mock import patch

from chatbot import config, engine as e
from chatbot.adapters import ApiAdapter, CachedAdapter, DataUnavailable, iso, now
from chatbot.catalog import Catalog
import test_v2153_payment_block as fixtures


class LateBlockResponseTests(unittest.TestCase):
    setUp = fixtures.ServerPaymentBlockTests.setUp
    tearDown = fixtures.ServerPaymentBlockTests.tearDown
    posts = fixtures.ServerPaymentBlockTests.posts
    ask = fixtures.ServerPaymentBlockTests.ask
    fee_signal = fixtures.ServerPaymentBlockTests.fee_signal
    assert_blocked = fixtures.ServerPaymentBlockTests.assert_blocked

    def test_same_id_blocked_replay_retains_safety_without_second_post(self):
        self.session_id = 'session-01'
        self.payment_code = 'EVACUATION_ACTIVE'
        question = self.ask('11번 결제')
        args = {'chip_id': 'confirm_yes', 'confirmation_token': question['confirmation_token'],
                'request_id': 'late-block'}
        first = e.handle('s', **args)
        replay = e.handle('s', **args)
        self.assert_blocked(first)
        self.assert_blocked(replay)
        self.assertTrue(replay['meta']['replayed'])
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(first['safety'], replay['safety'])

    def test_post409_does_not_invent_alarm_location_or_level(self):
        self.session_id = 'session-01'
        self.alerts = []
        self.payment_code = 'EVACUATION_ACTIVE'
        question = self.ask('11번 결제')
        result = e.handle('s', chip_id='confirm_yes', confirmation_token=question['confirmation_token'])
        self.assert_blocked(result)
        self.assertEqual(result['banner']['level'], 0)
        self.assertIn('지정 출구', result['safety']['guidance'])
        for key in ('lot_id', 'space_id', 'started_at', 'level'):
            self.assertNotIn(key, result['safety'])
        self.assertEqual(e.current_alert()['level'], 0)

    def test_block_evidence_does_not_leak_to_next_request_or_session(self):
        self.session_id = 'session-01'
        self.alerts = []
        with self.fee_signal(True):
            self.assert_blocked(self.ask('11번 결제'))
        self.adapter.cache.clear()
        for sid in ('s', 'other-session'):
            result = e.handle(sid, text='안녕하세요')
            self.assertIsNone(result['safety'])
            self.assertIsNone(result['banner'])
        self.assertIsNone(e._answer_evidence.get())

    def test_new_confirmation_is_not_cancelled_by_old_blocked_replay(self):
        self.session_id = 'session-01'
        self.alerts = []
        self.ask('11번 차 어디 있어?')
        with self.fee_signal(True):
            blocked = e.handle('s', text='결제해줘', request_id='old-block')
        self.assert_blocked(blocked)
        self.adapter.cache.clear()
        current = e.handle('s', text='결제해줘')
        self.assertEqual(current['flow'], 'confirm_action')
        replay = e.handle('s', text='결제해줘', request_id='old-block')
        self.assertIsNone(replay['safety'])
        self.assertEqual(replay['confirmation_token'], current['confirmation_token'])
        self.assertEqual(self.posts(), [])
        self.assertNotIn('payment_block_evidence', replay)


class OccupancyPublicationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(deepcopy(config.catalog.settings))
        self.patch = patch.object(config, 'catalog', self.catalog)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.before = self.catalog.public()
        self.version = self.catalog.version
        self.labels = list(reversed(self.catalog.spaces('A')))
        self.lots = {'items': [{'lot_id': 'A', 'total_spaces': len(self.labels),
                               'empty_spaces': len(self.labels)}], 'as_of': iso(now())}
        self.spaces = {'items': [{'space_id': label, 'status': 'empty', 'car_id': None,
                                  'space_type': 'general'} for label in self.labels]}
        self.api = ApiAdapter('http://unused.invalid')
        self.adapter = CachedAdapter(self.api)

    def request(self, method, path, body=None):
        return 200, deepcopy(self.lots if path == config.API_PATHS['lots'] else self.spaces), {}

    def read(self):
        with patch.object(self.api, '_request', side_effect=self.request):
            return self.adapter.get_empty_spots()

    def assert_unchanged(self):
        self.assertEqual(self.catalog.public(), self.before)
        self.assertEqual(self.catalog.version, self.version)

    def test_future_reordered_snapshot_does_not_publish(self):
        self.lots['as_of'] = iso(now() + timedelta(hours=1))
        with self.assertRaises(DataUnavailable):
            self.read()
        self.assert_unchanged()

    def test_future_unrecognized_new_spaces_do_not_register(self):
        self.lots['as_of'] = iso(now() + timedelta(hours=1))
        self.lots['items'][0]['empty_spaces'] -= 1
        self.spaces['items'][0].update(space_id='Z-99', status='occupied')
        with self.assertRaises(DataUnavailable):
            self.read()
        self.assert_unchanged()
        self.assertIsNone(self.catalog.space_index('A', 'Z-99'))

    def test_invalid_second_lot_does_not_publish_first_lot(self):
        self.lots['items'].append({'lot_id': 'B', 'total_spaces': 'bad', 'empty_spaces': 0})
        with self.assertRaises(DataUnavailable):
            self.read()
        self.assert_unchanged()

    def test_valid_spaces_and_totals_publish_once_and_keep_old_view(self):
        old = self.catalog.public_snapshot()
        result = self.read()
        self.assertEqual(self.catalog.spaces('A'), self.labels)
        self.assertEqual(self.catalog.version, self.version + 1)
        self.assertEqual(old, self.before)
        self.assertEqual(result['lots']['A']['total'], len(self.labels))
        self.assertNotIn('catalog_spaces', result)
        self.assertEqual(list(self.adapter.cache['empty']), list(result))

    def test_aggregate_count_mismatch_does_not_adopt_space_order(self):
        self.lots['items'][0]['empty_spaces'] -= 1
        result = self.read()
        self.assertNotIn('zones', result['lots']['A'])
        self.assertEqual(self.catalog.spaces('A'), self.before['lots'][0]['spaces'])

    def test_mismatched_unrecognized_rows_keep_committed_indices(self):
        self.spaces['items'][0]['status'] = 'occupied'
        result = self.read()
        car = result['unrecognized'][0]
        self.assertEqual(car['space_id'], self.labels[0])
        self.assertEqual(car['spot_no'], self.catalog.space_index('A', car['space_id']))
        self.assertEqual(self.catalog.spaces('A'), self.before['lots'][0]['spaces'])

    def test_incomplete_and_duplicate_spaces_do_not_adopt(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                self.adapter.reset()
                rows = self.spaces['items']
                self.spaces['items'] = rows[:-1] + ([rows[0]] if duplicate else [])
                self.read()
                self.assertEqual(self.catalog.spaces('A'), self.before['lots'][0]['spaces'])
                self.spaces['items'] = rows

    def test_reset_during_fetch_rejects_pending_catalog_publication(self):
        entered, release = threading.Event(), threading.Event()
        original = self.api.get_empty_spots
        errors = []

        def delayed():
            result = original()
            entered.set()
            if not release.wait(3):
                raise AssertionError('test release missing')
            return result

        def fetch():
            try:
                self.adapter.get_empty_spots()
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.api, '_request', side_effect=self.request), \
                patch.object(self.api, 'get_empty_spots', side_effect=delayed):
            worker = threading.Thread(target=fetch)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.adapter.reset()
                after_reset = self.catalog.version
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DataUnavailable)
        self.assertEqual(self.catalog.version, after_reset)
        self.assertEqual(self.catalog.spaces('A'), self.before['lots'][0]['spaces'])
        self.assertEqual(self.adapter.cache, {})


if __name__ == '__main__':
    unittest.main()
