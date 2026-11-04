"""Unsettled server identifiers remain opaque; local zoning stays bounded."""
import unittest
from unittest.mock import patch
from chatbot import config, engine as e, templates as T
from chatbot.adapters import build_payment_body, iso, now
import test_api_contract as fixture


class RepoV28Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ApiContractTests('test_01_server_counts_and_uncertain')
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def test_session_status_only_adds_checkout_notice(self):
        # v2.9(D-02): closed(전환기 별칭 exited)는 "출차 완료" 안내만 덧붙이고, 금액·결제 상태는 바꾸지 않는다.
        # 그 밖의 값(active, 모르는 값, null, 배열, 객체)은 응답에 영향이 없다.
        self.f.session_id='opaque-session'
        original=self.f.api._request_uncached
        answers=[]
        for state in ('active','exited','closed','future-state',None,[],{}):
            def request(method,path,body=None):
                status,value,headers=original(method,path,body)
                if '/sessions/' in path:
                    value={**value,'session_status':state}
                return status,value,headers
            with patch.object(self.f.api,'_request_uncached',side_effect=request):
                r=self.f.ask('11번 요금')
            answers.append((r['text'],r['card']['data']['fee'],r['card']['data']['paid']))
        baseline=answers[0]
        for state,answer in zip(('active','exited','closed','future-state',None,[],{}),answers):
            self.assertEqual(answer[1:],baseline[1:])
            if state in ('exited','closed'):
                self.assertEqual(answer[0],'출차 완료된 주차예요. '+baseline[0])
            else:
                self.assertEqual(answer[0],baseline[0])

    def test_payment_body_has_exactly_four_contract_fields(self):
        # v2.9: quote_id 채택(D-09). billing PaymentCreateRequest의 네 필드만 보낸다.
        self.assertEqual(build_payment_body('session/value','quote-1','idempotent'),
            {'session_id':'session/value','quote_id':'quote-1','idempotency_key':'idempotent','payment_method':'MOCK_CARD'})
        self.f.pay()
        self.assertEqual(set(self.f.posts()[0][2]), {'session_id','quote_id','idempotency_key','payment_method'})

    def test_larger_server_lots_keep_unmapped_spaces_out_of_zones(self):
        original=self.f.api._request_uncached
        def request(method,path,body=None):
            if path.endswith('/parking-lots'):
                return 200,{'items':[{'lot_id':lot,'total_spaces':count,'empty_spaces':count,'uncertain_spaces':0,'unavailable_spaces':0}
                    for lot,count in [('A',16),('B',12)]],'as_of':iso(now())},{}
            if path.endswith('/spaces'):
                lot=path.split('/')[-2];count={'A':16,'B':12}[lot]
                return 200,{'items':[{'space_id':f'{lot}-{n:02d}','status':'empty'} for n in range(1,count+1)]},{}
            return original(method,path,body)
        with patch.object(self.f.api,'_request_uncached',side_effect=request):
            r=self.f.ask('충전 자리')
            # 서버 칸 이름 중 명부(도면)에 있는 칸만 구역에 넣는다: A는 A-01~A-06(전기차 A-05·A-06), B는 B-01~B-08(장애인 B-07·B-08)
            for lot,count,zoned,ev in [('A',16,6,2),('B',12,8,0)]:
                d=r['card']['data']['lots'][lot]
                self.assertEqual(d['total'],count)
                self.assertTrue(d['zone_partial'])
                self.assertEqual(sum(z['total'] for z in d['zones'].values()),zoned)
                self.assertEqual(d['zones']['ev']['total'],ev)
            self.assertIn('구역 정보가 일부 칸만 설정돼 있어요',r['text'])
            with patch.object(self.f.api,'_request',return_value=(200,{'car_id':'11','lot_id':'A','space_id':'A-16',
                'session_id':None,'status':'occupied','observed_at':iso(now())},{})):
                car=self.f.api.find_car('11')
            self.assertIn('개발원주차장 A-16',T.car_location(car))
        self.assertEqual([lot['capacity'] for lot in config.catalog.settings['lots']], [12,16,12,64])

    def test_current_out_of_range_server_identifier_is_not_rewritten(self):
        self.f.adapter.get_empty_spots()
        raw={'car_id':'25','lot_id':'A','space_id':'A-01','session_id':None,'status':'occupied','observed_at':iso(now())}
        with patch.object(self.f.api,'_request',return_value=(200,raw,{})):
            car = self.f.api.find_car('25')
            self.assertEqual(car['car_id'],'25')
        self.assertIn('25번',T.car_location(car))
