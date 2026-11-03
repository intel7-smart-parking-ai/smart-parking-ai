import time
import unittest
from unittest.mock import patch
import test_api_contract as fixture_module
from robust_support import FaultServer
from chatbot import engine as e
from chatbot.adapters import ApiAdapter, CachedAdapter


class RequestDedupeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture_module.ApiContractTests('test_01_server_counts_and_uncertain')
        self.f.setUp()
    def tearDown(self): self.f.tearDown()
    def test_specific_lot_and_request_lifetime(self):
        for i in range(2):
            start=len(self.f.requests)
            self.f.ask('개발원주차장 빈자리')
            paths=[x[1] for x in self.f.requests[start:]]
            self.assertEqual(paths.count('/api/v1/parking-lots'),1)
            self.assertEqual(paths.count('/api/v1/parking-lots/A/spaces'),1)
            self.assertNotIn('/api/v1/parking-lots/B/spaces',paths)
            self.assertNotIn('/api/v1/parking-lots/C/spaces',paths)
    def test_zone_queries_all_lots_once(self):
        self.f.ask('전기차 자리 있어?')
        paths=[x[1] for x in self.f.requests]
        self.assertEqual(paths.count('/api/v1/parking-lots'),1)
        for lot in 'ABC': self.assertEqual(paths.count('/api/v1/parking-lots/'+lot+'/spaces'),1)
    def test_fee_location_once(self):
        self.f.ask('11번 차 요금')
        self.assertEqual(sum(x[1]=='/api/v1/vehicles/11/location' for x in self.f.requests),1)
    def test_slow_server_one_aggregate_budget(self):
        server=FaultServer();server.target='lots';server.mode='headers'
        try:
            e.data=CachedAdapter(ApiAdapter(server.base))
            e.get_session('s').car_id='11'
            started=time.monotonic();r=e.handle('s',text='요금')
            self.assertLess(time.monotonic()-started,2.5)
            self.assertIn('현재 요금 정보를 확인하지 못했어요',r['text'])
            self.assertEqual(sum(path=='/api/v1/parking-lots' for _,path in server.requests),1)
        finally:server.close()
    def test_fee_failure_reasons(self):
        self.assertIn('아직 요금 정보를 확인할 수 없어요',self.f.ask('11번 요금')['text'])
        self.f.session_id='session-01';original=self.f.api._request_uncached
        for code,body,expected in [(503,{},'현재 요금 정보를 확인하지 못했어요'),(404,{'detail':'Not Found'},'요금 조회 기능이 아직 연결되지 않았어요'),(404,{'error':{'code':'SESSION_NOT_FOUND'}},'아직 요금 정보를 확인할 수 없어요')]:
            def request(method,path,body_arg=None):
                return (code,body,{}) if '/sessions/' in path else original(method,path,body_arg)
            with patch.object(self.f.api,'_request_uncached',side_effect=request):
                for question in ('요금','결제'):
                    self.assertIn(expected,e.handle('s',text=question)['text'])
    def test_alert_notice_once_recovery_and_explicit_query(self):
        answers=[self.f.ask('A 빈자리')['text']for _ in range(3)]
        self.assertEqual(sum('경보 상태를 확인할 수 없어요' in t for t in answers),1)
        self.assertIn('경보 상태를 확인할 수 없어요',self.f.ask('A 빈자리','new')['text'])
        for _ in range(2):self.assertIn('경보 상태를 확인할 수 없어요',self.f.ask('경보')['text'])
        self.f.alerts=[];e.updates('s');self.f.alerts=None
        self.assertIn('경보 상태를 확인할 수 없어요',self.f.ask('A 빈자리')['text'])
    def test_missing_car_notice_is_explicit_action_only(self):
        answer=self.f.ask('15번 차 어디 있어?')
        self.assertNotIn('목록도 확인하지 못',answer['text'])
        self.assertTrue(any(c['id']=='unrecognized' for c in answer['chips']))
        self.f.session_id='session-01';e.get_session('s').car_id='11'
        self.f.ask('결제')
        self.assertIn('목록을 확인하지 못했어요',e.handle('s',chip_id='unrecognized')['text'])
        self.assertIsNone(e.get_session('s').pending)
