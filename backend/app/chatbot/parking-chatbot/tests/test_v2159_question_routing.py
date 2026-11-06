"""Screenshot regressions: presence/help and safe unsupported group queries."""
import unittest
from unittest.mock import patch
from chatbot import engine as e, parser
import test_api_contract as fixtures


class QuestionParserTests(unittest.TestCase):
    def test_presence_questions_use_existing_location_intent(self):
        for text, car in [('30번 차량은 있나?','30'),('30번 차 있어?','30'),
                          ('삼십 번 차량 있나요?','30'),('11번 말고 30번 차량은 있나?','30'),
                          ('23번 차량은 현재 주차되어 있나요?','23'),('23번 입차해 있나요?','23')]:
            with self.subTest(text=text):
                p=parser.parse(text,allow_llm=False)
                self.assertEqual((p.intent,p.params.get('car_id')),('find_my_car',car))
        self.assertEqual(parser.parse_rules('내 차 있나요?').intent,'find_my_car')

    def test_capability_questions_are_help_without_selecting_sample_vehicle(self):
        for text in ['내가 저 친내용 말고 질문할 수 있는 내용이 있나/',
                     '무슨 질문 할 수 있어?','어떤 질문이 가능해요?',
                     '뭐 물어보면 돼?','물어볼 수 있는 내용 알려줘',
                     '23번 위치 말고 질문할 수 있는 내용이 있나?']:
            with self.subTest(text=text):
                p=parser.parse_rules(text)
                self.assertEqual(p.intent,'help')
                self.assertTrue(p.params.get('help_examples'))
                self.assertNotIn('car_id',p.params)

    def test_range_and_list_are_not_one_vehicle_or_a_bulk_action(self):
        for text,scope in [('지금 들어와 있는 차량은 뭐 있는지 확인가능한 20번대','range'),
                           ('20~30번 안에 차량 번호 있는게 있나?','range'),
                           ('20부터 30번까지 차량 있나?','range'),('이십 번대 차량 있나요?','range'),
                           ('20~30번 차량 전부 결제해줘','range'),
                           ('20번대 차량 관리자 불러줘','range'),
                           ('현재 들어와 있는 차량은 뭐 있나요?','list'),
                           ('전체 차량 목록 알려줘','list')]:
            with self.subTest(text=text):
                p=parser.parse_rules(text)
                self.assertEqual((p.intent,p.params.get('vehicle_query_help')),('help',scope))
                self.assertNotIn('car_id',p.params)

    def test_alarm_cancellation_and_existing_boundaries_keep_priority(self):
        for text,intent in [('20번대 차량 결제하지 마','cancel_action'),
                            ('20번대 차량 경보 확인','get_alerts'),
                            ('20번대 차량 대피 안내','evacuation_guide'),
                            ('20~30분 요금 얼마야?','get_fee'),
                            ('B-06 위치','get_empty_spots'),('30번 결제하지 마','cancel_action')]:
            with self.subTest(text=text):self.assertEqual(parser.parse_rules(text).intent,intent)
        for text in ['30번 차량은 있나 말고','30번 차량은 있는지 알려주지 마',
                     '30번 칸 있나요?','23번은 빼고 출구 있나요?']:
            with self.subTest(text=text):self.assertNotEqual(parser.parse_rules(text).intent,'find_my_car')

    def test_new_routes_do_not_need_a_model(self):
        with patch('chatbot.llm.classify',side_effect=AssertionError('unexpected paid/model route')):
            for text in ['30번 차량은 있나?','무슨 질문 할 수 있어?','20~30번 차량 있나요?']:
                with self.subTest(text=text):self.assertNotEqual(parser.parse(text).intent,'unknown')


class QuestionHTTPTests(unittest.TestCase):
    setUp=fixtures.ApiContractTests.setUp
    tearDown=fixtures.ApiContractTests.tearDown
    ask=fixtures.ApiContractTests.ask
    posts=fixtures.ApiContractTests.posts

    def test_presence_requires_confirmation_then_reads_official_location(self):
        r=e.handle('s',text='11번 차량은 있나?')
        self.assertEqual(r['flow'],'confirm_vehicle')
        self.assertFalse(any('/vehicles/' in path for _,path,_ in self.requests))
        r=e.handle('s',chip_id='confirm_yes',confirmation_token=r['confirmation_token'])
        self.assertEqual(r['card']['type'],'car_location')
        self.assertEqual(r['card']['data']['car_id'],'11')
        self.assertIn(('GET','/api/v1/vehicles/11/location',None),self.requests)
        self.assertEqual(self.posts(),[])

    def test_no_entry_remains_authoritative_not_replaced_with_mock(self):
        r=self.ask('30번 차량은 있나?')
        self.assertIn(('GET','/api/v1/vehicles/30/location',None),self.requests)
        self.assertIn('입차 기록이 없어요',r['text'])
        self.assertIsNone(r['card'])
        self.assertEqual(self.posts(),[])

    def test_help_and_range_explain_limits_without_vehicle_or_payment_calls(self):
        for text,part in [('무슨 질문 할 수 있어?','예:'),
                          ('20~30번 안에 차량 번호 있는게 있나?','범위'),
                          ('현재 들어와 있는 차량은 뭐 있나요?','전체')]:
            with self.subTest(text=text):
                r=e.handle('s',text=text)
                self.assertIn(part,r['text'])
                if part!='예:':
                    self.assertIn('두 자리',r['text'])
                self.assertIsNone(r['card'])
        self.assertFalse(any('/vehicles/' in path or '/sessions/' in path for _,path,_ in self.requests))
        self.assertEqual(self.posts(),[])

    def test_group_query_clears_pending_payment_and_old_approval_cannot_pay(self):
        self.session_id='session-01'
        r=self.ask('11번 결제');token=r['confirmation_token']
        self.assertEqual(r['flow'],'confirm_action')
        r=e.handle('s',text='20~30번 차량 전부 결제해줘')
        self.assertIn('범위',r['text'])
        self.assertIsNone(e.get_session('s').pending)
        e.handle('s',chip_id='confirm_yes',confirmation_token=token)
        self.assertEqual(self.posts(),[])
        self.assertEqual(e.get_session('s').car_id,'11')


if __name__=='__main__':unittest.main()
