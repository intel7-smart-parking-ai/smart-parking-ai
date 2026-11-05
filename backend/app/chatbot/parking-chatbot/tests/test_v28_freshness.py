"""Response timestamp policy; bounded cache and alarm safety remain separate."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from chatbot import config, engine as e, templates as T
from chatbot.adapters import ApiAdapter, CachedAdapter, MockAdapter, DataUnavailable, iso


class FreshnessV28Tests(unittest.TestCase):
    def setUp(self):
        self.settings = deepcopy(config.catalog.settings)
        self.fixed = datetime(2026, 11, 3, 14, 2, tzinfo=config.KST)
        self.api = ApiAdapter('http://unused')
        self.cached = CachedAdapter(self.api)
        self.cached.catalog_ready = True
        self.mock = MockAdapter()
        self.clock = patch('chatbot.adapters.now', return_value=self.fixed)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        config.catalog.settings = self.settings
        config.catalog.reset()

    def value(self, kind, seconds=300, **extra):
        stamp = iso(self.fixed - timedelta(seconds=seconds))
        if kind == 'alert':
            return dict(level=2, version=1, lot_id='A', spot_no=None,
                        started_at=stamp, updated_at=stamp, api_result=True, **extra)
        d = {'empty': self.mock.get_empty_spots, 'car': lambda: self.mock.find_car('33'),
             'fee': lambda: self.mock.get_fee('33')}[kind]()
        return {**d, 'api_result': True, 'payment_status': 'unpaid', 'is_daily_capped': False,
                'updated_at': stamp, **extra}

    def fetch(self, kind, d):
        key = kind if kind in ('empty', 'alert') else kind+':17'
        return self.cached._call(key, lambda *args: deepcopy(d), *(() if ':' not in key else ('33',)))

    # v2.9: 신선도 세부 규칙(STALE_POLICY, "N분 전 기준" 표기)은 제거했다. 아래는 남긴 안전·연결 규칙이다.
    def test_old_general_queries_are_answered_without_age_note(self):
        for kind in ('empty', 'car', 'fee'):
            d = self.fetch(kind, self.value(kind))
            self.assertNotIn('분 전', T.data_notes('정상 답', d))
        self.assertFalse(hasattr(config, 'STALE_POLICY'))

    def test_future_timestamps_are_rejected(self):
        for kind in ('empty', 'car', 'fee', 'alert'):
            with self.assertRaises(DataUnavailable):
                self.fetch(kind, self.value(kind, -10, data_source='mock'))

    def test_old_all_clear_is_unavailable_but_old_active_keeps_safety(self):
        self.cached.cache.clear()
        with self.assertRaises(DataUnavailable):
            self.fetch('alert', {**self.value('alert'), 'level': 0})
        for level in (1, 2):
            d = self.fetch('alert', {**self.value('alert'), 'level': level})
            self.assertEqual(d['level'], level)
            if level == 2:
                self.assertIn('차를 옮기려 하지 마세요', T.api_alert(d))

    def test_fee_accepts_real_billing_target_at(self):
        car = {'session_id': 's', 'updated_at': iso(self.fixed), 'car_id': '33', 'lot_id': 'A'}
        fee = {'amount_krw': 1000, 'duration_minutes': 30, 'entry_at': iso(self.fixed), 'exit_at': None,
               'payment_status': 'unpaid', 'target_at': iso(self.fixed-timedelta(minutes=5)),
               'rate_policy': {'base_minutes': 30, 'base_fee': 1000, 'unit_minutes': 10, 'extra_fee': 500, 'daily_cap': 10000},
               'quote_id': 'q1', 'quote_expires_at': iso(self.fixed+timedelta(minutes=5))}
        with patch.object(self.api, 'find_car', return_value=car), patch.object(self.api, '_request', return_value=(200, fee, {})):
            d = self.cached.get_fee('33')
        self.assertEqual(d['updated_at'], fee['target_at'])
        self.assertEqual(d['quote_id'], 'q1')
        self.assertEqual(d['session_status'], 'active')
        # calculated_at·as_of·target_at이 모두 없어도 요금 답을 막지 않는다(v2.8 결함 수정).
        for key in ('target_at',):
            fee.pop(key)
        self.cached.cache.clear()
        with patch.object(self.api, 'find_car', return_value=car), patch.object(self.api, '_request', return_value=(200, fee, {})):
            self.assertEqual(self.cached.get_fee('33')['fee'], 1000)

    def test_engine_mock_notice_once_without_age_line(self):
        d = self.value('empty', data_source='mock')
        with patch.object(e, 'data', self.cached), patch.object(config, 'DATA_SOURCE', 'api'), patch.object(config, 'ALERT_SHARE_SEC', 0):
            e.reset_all()
            with patch.object(self.api, 'get_empty_spots', return_value=d), patch.object(self.api, 'get_alert', return_value={'level': 0, 'version': 0, 'api_result': True, 'updated_at': iso(self.fixed)}):
                r = e.handle('v28', text='A 빈자리')
        self.assertEqual(r['text'].count('시연용 데이터'), 1)
        self.assertNotIn('분 전 기준', r['text'])

    def test_invalid_all_clear_cannot_erase_last_safe_alarm(self):
        for seconds in (300, -10):
            api = ApiAdapter('http://unused')
            cached = CachedAdapter(api)
            cached.catalog_ready = True
            active = {'items':[{'alert_id':'safety','lot_id':'A','space_id':None,'level':2,
                'status':'active','type':'mock_fire_led'}], 'as_of':iso(self.fixed)}
            clear = {'items':[], 'as_of':iso(self.fixed-timedelta(seconds=seconds))}
            with patch.object(api, '_request', side_effect=[(200,active,{}),(200,clear,{}),(503,{},{} )]):
                self.assertEqual(cached.get_alert()['level'],2)
                with self.assertRaises(DataUnavailable):
                    cached.get_alert()
                failed = cached.get_alert()
                self.assertTrue(failed['unavailable'])
                self.assertEqual(failed['level'],2)
