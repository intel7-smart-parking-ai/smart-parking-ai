"""Integration boundaries: short speech, authoritative receipts, safety priority."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch
from chatbot import parser, engine as e
from chatbot.adapters import ApiAdapter, DataUnavailable, iso, now
import test_api_contract as fixtures


class ShortLocationTests(unittest.TestCase):
    def test_short_and_corrected_targets(self):
        for text, expected in [('23번 위치','23'),('23번 위치 알려줘','23'),
                ('이십 삼 번 위치','23'),('23번 말고 30번 위치','30'),
                ('이십 삼 번 말고 십 일 번 위치','11')]:
            with self.subTest(text=text):
                result=parser.parse_rules(text)
                self.assertEqual((result.intent,result.params.get('car_id')),('find_my_car',expected))

    def test_existing_guard_and_identity_boundaries(self):
        for text in ['위치','23번 위치 알려주지 마','23번 위치 확인하지 마',
                     '23번 위치 말고','23번과 30번 위치','23번 말고 위치','23번 칸 위치',
                     '23번은 빼고 출구 위치','23번은 됐고 엘리베이터 위치',
                     '23번이 주차된 곳이 아니라 화장실 위치']:
            with self.subTest(text=text):self.assertNotEqual(parser.parse_rules(text).intent,'find_my_car')
        self.assertEqual(parser.parse_rules('B-06 위치').intent,'get_empty_spots')
        self.assertEqual(parser.parse_rules('23번 위치 말고 요금').intent,'get_fee')
        self.assertEqual(parser.parse_rules('23번 결제하지 마').intent,'cancel_action')


class ReceiptValidationTests(unittest.TestCase):
    def setUp(self):
        self.api=ApiAdapter('http://unused.invalid')
        self.car={'car_id':'23','lot_id':'A','session_id':'visit-23'}
        self.receipt={'payment_id':'pay-23','session_id':'visit-23','status':'completed',
                      'amount_krw':2500,'created_at':iso(now())}
        self.value={'payment_status':'completed','amount_krw':2500,'payment':self.receipt}

    def test_valid_and_legacy_optional_receipts(self):
        result=self.api._fee_receipt(self.value,self.car)
        self.assertEqual((result['payment_id'],result['amount'],result['status']),('pay-23',2500,'already_paid'))
        self.assertIsNone(self.api._fee_receipt({'payment':None},self.car))
        self.assertIsNone(self.api._fee_receipt({},self.car))

    def test_invalid_receipts_fail_closed(self):
        changes=[{'session_id':'another'},{'car_id':'11'},{'lot_id':'B'},
                 {'amount_krw':True},{'amount_krw':-1},{'amount_krw':2501},
                 {'payment_id':''},{'session_id':None},{'status':'canceled'},
                 {'created_at':'not-a-time'},{'created_at':'2026-11-06T09:00:00'},
                 {'created_at':iso(now()+timedelta(hours=1))}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(DataUnavailable):
                self.api._fee_receipt({**self.value,'payment':{**self.receipt,**change}},self.car)
        for payment in ([],False,'bad'):
            with self.subTest(payment=payment),self.assertRaises(DataUnavailable):
                self.api._fee_receipt({**self.value,'payment':payment},self.car)
        with self.assertRaises(DataUnavailable):
            self.api._fee_receipt({**self.value,'payment_status':'unpaid'},self.car)


class ReceiptHTTPTests(unittest.TestCase):
    setUp=fixtures.ApiContractTests.setUp
    tearDown=fixtures.ApiContractTests.tearDown
    posts=fixtures.ApiContractTests.posts
    ask=fixtures.ApiContractTests.ask

    def fee_receipt(self, *, blocked=False, bad_visit=False):
        self.session_id='session-01';self.payment_status='completed'
        original=self.api._request
        def request(method,path,body=None):
            status,value,headers=original(method,path,body)
            if method=='GET' and path.startswith('/api/v1/sessions/'):
                value={**value,'payment_blocked':blocked,'payment':{
                    'payment_id':'receipt-original','session_id':'wrong' if bad_visit else self.session_id,
                    'status':'completed','amount_krw':2500,'created_at':iso(now())}}
            return status,value,headers
        return patch.object(self.api,'_request',side_effect=request)

    def test_short_location_reads_explicit_vehicle_once_without_confirmation(self):
        response=e.handle('s',text='11번 위치')
        self.assertEqual(response['flow'],'idle')
        self.assertEqual(response['card']['data']['car_id'],'11')
        self.assertIsNone(response['confirmation_token'])
        self.assertEqual(self.requests.count(('GET','/api/v1/vehicles/11/location',None)),1)

    def test_fee_and_payment_requery_restore_existing_receipt_without_post(self):
        with self.fee_receipt():
            for text in ['11번 요금','결제해줘']:
                response=self.ask(text)
                self.assertEqual(response['card']['type'],'payment')
                self.assertEqual(response['card']['data']['payment_id'],'receipt-original')
                self.assertEqual(response['card']['data']['amount'],2500)
                self.assertIsNone(e.get_session('s').pending)
        self.assertEqual(self.posts(),[])

    def test_completed_receipt_never_overrides_server_block(self):
        for bad_visit in (False, True):
            self.adapter.cache.clear()
            with self.subTest(bad_visit=bad_visit), self.fee_receipt(blocked=True,bad_visit=bad_visit):
                response=self.ask('11번 요금')
                self.assertIsNotNone(response['safety'])
                self.assertEqual(response['card']['type'],'fee')
                self.assertTrue({'pay','confirm_yes'}.isdisjoint(c['id'] for c in response['chips']))
        self.assertEqual(self.posts(),[])

    def test_conflicting_receipt_does_not_fall_back_to_cached_fee(self):
        with self.fee_receipt():self.adapter.get_fee('11')
        with self.fee_receipt(bad_visit=True),self.assertRaises(DataUnavailable):self.adapter.get_fee('11')

    def test_stale_snapshot_is_not_new_verified_receipt(self):
        with self.fee_receipt():value=self.adapter.get_fee('11')
        card=e._fee_card({**deepcopy(value),'stale':True})
        self.assertEqual(card['type'],'fee')


if __name__=='__main__':unittest.main()
