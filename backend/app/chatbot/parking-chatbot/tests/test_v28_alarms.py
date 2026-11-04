"""Nullable team alarms and response-local notices over the existing HTTP fixture."""
import unittest
from unittest.mock import patch
from chatbot import engine as e, briefing, navigation as N
import test_api_contract as fixture


class AlarmV28Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ApiContractTests('test_01_server_counts_and_uncertain')
        self.f.setUp()
        self.f.mock_header = False

    def tearDown(self):
        self.f.tearDown()

    def alarm(self, lot='A', space='A-03', level=1, status='active', **extra):
        self.f.alerts = [dict(alert_id='v28', lot_id=lot, space_id=space, level=level,
            status=status, type='mock_fire_led', occurred_at='2026-11-03T05:02:00+00:00', **extra)]

    def test_null_spot_preserves_lv2_and_whole_lot_scope(self):
        self.alarm(space=None, level=2, status='acknowledged')
        r = self.f.ask('경보')
        self.assertIn('사람 먼저', r['safety']['title'])
        self.assertIn('개발원주차장(정확한 칸 확인 중)', r['text'])
        self.assertNotIn('번 칸', r['text'])
        self.assertNotIn('None', r['text'])
        a = e.current_alert()
        self.assertEqual(N.reason({'lots': {'A': {'empty': 1}}}, a, 'A'), '경보')
        self.assertIn('전체 위험 범위', e.incidents.active()['briefing']['text'])
        self.assertIn('전체를 위험 구역', e.T.alert_near(a, None))
        self.assertTrue(e.updates('s')['messages'])

    def test_unknown_lot_lv1_and_unknown_lot_lv2_blocks_all(self):
        self.alarm(lot=None, space=None)
        self.assertIn('주차장 위치 확인 중', self.f.ask('경보')['text'])
        a = {**e.current_alert(), 'level': 2}
        self.assertEqual(N.reason({'lots': {'B': {'empty': 1}}}, a, 'B'), '경보')
        cars = [{'car_id': '13', 'spot_no': 6}]
        b = briefing.build(a, cars, True)
        self.assertNotIn('None번', b['text'])
        self.assertIn('필요 가능성', b['equipment'])

    def test_api_alarm_wording_active_ack_resolved_and_empty(self):
        self.f.alerts = []
        self.assertEqual(self.f.ask('경보')['text'], '현재 모의 화재 경보는 없어요.')
        self.alarm()
        r = self.f.ask('경보')['text']
        self.assertIn('모의 화재(LED) 경보가 발생했어요 (14:02)', r)
        self.assertNotIn('아직 경보', r)
        self.assertNotIn('관리자가 확인', r)
        self.alarm(status='acknowledged')
        self.assertIn('관리자가 확인 중이에요.', self.f.ask('경보')['text'])
        self.alarm(status='resolved')
        self.assertEqual(self.f.ask('경보')['text'], '해당 모의 화재 경보는 해제됐어요.')
        self.assertEqual(self.f.ask('경보')['text'], '현재 모의 화재 경보는 없어요.')

    def test_missing_malformed_time_and_both_types(self):
        for kind in ('mock_fire_led', 'mock_fire_led_observed'):
            for value in (None, 'broken', '2026-11-03T14:02:00'):
                self.alarm()
                self.f.alerts[0].update(type=kind, occurred_at=value)
                if value is None:
                    self.f.alerts[0].pop('occurred_at')
                text = self.f.ask('경보')['text']
                self.assertIn('발생했어요.', text)
                self.assertNotIn('(14:02)', text)

    def test_unknown_type_ignored_once(self):
        self.alarm()
        self.f.alerts[0]['type'] = 'unrelated'
        with self.assertLogs('chatbot.adapters', level='WARNING') as logs:
            r = self.f.ask('경보')
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(r['text'], '현재 모의 화재 경보는 없어요.')

    def test_mock_notice_comes_from_answer_not_lots_or_prior_request(self):
        self.f.mock_header = True
        self.assertIn('시연용 데이터', self.f.ask('A 빈자리')['text'])
        self.f.mock_header = False
        self.assertNotIn('시연용 데이터', self.f.ask('경보')['text'])
        # /lots is mock but the failing /alerts response carries no such header.
        original = self.f.api._request_uncached
        def request(method, path, body=None):
            status, value, headers = original(method, path, body)
            if path.endswith('/alerts'):
                return status, value, {}
            return status, value, {'X-ParkFlow-Data-Source': 'mock'}
        e.reset_all()
        with patch.object(self.f.api, '_request_uncached', side_effect=request):
            self.assertNotIn('시연용 데이터', self.f.ask('경보')['text'])

    def test_invalid_input_does_not_consume_first_unavailable_notice(self):
        self.assertNotIn('경보 상태를 확인할 수 없어요', self.f.ask('5번')['text'])  # 두 자리 규칙 밖 = 입력 오류
        self.assertIn('경보 상태를 확인할 수 없어요', self.f.ask('A 빈자리')['text'])
        self.assertNotIn('경보 상태를 확인할 수 없어요', self.f.ask('A 빈자리')['text'])

    def test_failure_header_does_not_inherit_prior_mock_alarm_header(self):
        self.f.mock_header = True
        self.alarm(level=2)
        self.assertIn('시연용 데이터', self.f.ask('경보')['text'])
        self.f.mock_header = False
        self.f.alerts = None
        r = self.f.ask('경보')
        self.assertIsNotNone(r['safety'])
        self.assertNotIn('시연용 데이터', r['text'])
