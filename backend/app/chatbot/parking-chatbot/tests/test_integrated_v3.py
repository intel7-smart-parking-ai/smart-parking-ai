"""통합요청 v3 A: 실행을 유발하던 부정 표현과 요금 장애·구역 오분류 회귀 검사."""
import csv
from pathlib import Path
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, parser
from chatbot.adapters import CachedAdapter, MockAdapter


class RehearsalRuleTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(e,'data',CachedAdapter(MockAdapter()))
        p.start();self.addCleanup(p.stop)
        p=patch.object(config,'LLM_PROVIDER','none')
        p.start();self.addCleanup(p.stop)
        e.reset_all()

    def test_rehearsal_csv_intents_and_arguments(self):
        with Path(__file__).with_name('questions_rehearsal.csv').open(encoding='utf-8') as stream:
            for row in csv.DictReader(stream):
                with self.subTest(question=row['question']):
                    got=parser.parse_rules(row['question'])
                    self.assertEqual(got.intent,row['intent'])
                    for key in ('lot_id','car_id','zone'):
                        self.assertEqual(got.params.get(key,''),row[key])

    def test_admin_negative_does_not_create_call_even_with_wrong_llm_choice(self):
        with patch('chatbot.llm_router.route',return_value={'intent':'call_admin','params':{}}) as route:
            got=e.handle('s',text='직원분 안 오셔도 될 것 같아요')
        self.assertEqual(e.admin_calls(),[])
        self.assertEqual(got['meta']['intent'],'cancel_action')
        self.assertEqual(got['meta']['source'],'rule')
        self.assertIn('새 관리자 호출은 하지 않을게요',got['text'])
        route.assert_not_called()

    def test_admin_honorific_negative_variants(self):
        for text in ('직원분 안 와도 돼요','관리자님 안 오셔도 돼요','담당자분 오실 필요 없어요',
                     '직원 안 오셔도 될 것 같아요','관리자님은 안 와도 돼요'):
            with self.subTest(text=text):
                got=parser.parse_rules(text)
                self.assertEqual((got.intent,got.params.get('cancel_target')),('cancel_action','admin'))
                e.handle('s',text=text)
        self.assertEqual(e.admin_calls(),[])

    def test_explicit_admin_requests_still_register_immediately(self):
        for sid,text in enumerate(('관리자 불러줘','관리자님 좀 불러주세요','직원분 불러 주세요')):
            with self.subTest(text=text):
                got=e.handle(str(sid),text=text)
                self.assertEqual(got['meta']['intent'],'call_admin')
                self.assertEqual(got['card']['type'],'admin_call')
        self.assertEqual(len(e.admin_calls()),3)

    def test_payment_postponement_has_payment_target(self):
        for text in ('돈은 이따 낼게요','결제는 나중에 할게요','다음에 낼게요','계산은 이따 할게요',
                     '이따가 낼게요','나중에 결제할게요','아직 계산은 안 할래'):
            with self.subTest(text=text):
                got=parser.parse_rules(text)
                self.assertEqual((got.intent,got.params.get('cancel_target')),('cancel_action','payment'))

    def test_postponement_cancels_pending_payment_and_old_confirmation(self):
        e.handle('s',text='17번 결제')
        quote=e.handle('s',text='네')
        self.assertEqual(quote['flow'],'confirm_action')
        with patch.object(e.data,'pay',wraps=e.data.pay) as pay:
            got=e.handle('s',text='돈은 이따 낼게요')
            self.assertEqual(got['flow'],'idle')
            e.handle('s',chip_id='confirm_yes',confirmation_token=quote['confirmation_token'])
            e.handle('s',text='네')
            pay.assert_not_called()
        self.assertFalse(e.data.get_fee('17')['paid'])

    def test_immediate_payment_and_later_exit_are_not_postponed(self):
        for text in ('돈 지금 낼게요','지금 결제할게요','이따가 나갈 건데 지금 결제할게요'):
            with self.subTest(text=text):self.assertEqual(parser.parse_rules(text).intent,'pay_fee')

    def test_duplicate_payment_reports_are_issues_and_never_new_payments(self):
        for text in ('돈이 두 번 빠져나간 것 같아','이중 결제됐어요','중복 결제됐어요','요금이 12번 빠져나갔어요'):
            with self.subTest(text=text):
                self.assertEqual(parser.parse_rules(text).intent,'payment_issue')
                with patch.object(e.data,'pay',wraps=e.data.pay) as pay:
                    got=e.handle('s',text=text)
                    pay.assert_not_called()
                self.assertEqual(got['meta']['intent'],'payment_issue')
                self.assertIsNone(got['confirmation_token'])

    def test_fee_status_queries_are_not_payment_issues(self):
        for text in ('결제 상태 확인','요금 얼마야?','결제 완료됐어?'):
            with self.subTest(text=text):self.assertEqual(parser.parse_rules(text).intent,'get_fee')

    def test_wheelchair_mark_selects_disabled_zone(self):
        got=e.handle('s',text='휠체어 마크 있는 칸 비었어?')
        self.assertEqual(got['meta']['intent'],'get_empty_spots')
        self.assertEqual(got['card']['data']['zone'],'accessible')
        self.assertIn('장애인',got['text'])
        self.assertEqual(parser.parse_rules('B 휠체어 자리').params,{'lot_id':'B','zone':'accessible'})


class LLMEvaluationExpansionTests(unittest.TestCase):
    def read_rows(self, name):
        with Path(__file__).with_name(name).open(encoding='utf-8') as stream:
            return list(csv.DictReader(stream))

    def test_llm_set_is_unknown_only_with_no_duplicates(self):
        rows=self.read_rows('llm_eval_questions.csv')
        self.assertEqual(len(rows),35)
        self.assertEqual(len({r['question'] for r in rows}),35)
        for row in rows:
            with self.subTest(question=row['question']):
                self.assertEqual(parser.parse_rules(row['question']).intent,'unknown')
        self.assertEqual(sum(row['intent']=='unknown' for row in rows),5)

    def test_newly_recognized_negative_moves_to_rehearsal(self):
        llm=self.read_rows('llm_eval_questions.csv')
        rehearsal=self.read_rows('questions_rehearsal.csv')
        self.assertEqual(len(rehearsal),5)
        self.assertNotIn('아직 계산은 안 할래',{r['question'] for r in llm})
        row=next(r for r in rehearsal if r['question']=='아직 계산은 안 할래')
        self.assertEqual(row['intent'],'cancel_action')
        self.assertEqual(parser.parse_rules(row['question']).params['cancel_target'],'payment')

    def test_regression_glob_contains_eight_sets_and_no_llm_set(self):
        files=list(Path(__file__).parent.glob('questions*.csv'))
        self.assertEqual(len(files),8)
        self.assertEqual(sum(len(self.read_rows(f.name)) for f in files),242)
        self.assertNotIn('llm_eval_questions.csv',{f.name for f in files})


if __name__=='__main__':unittest.main()
