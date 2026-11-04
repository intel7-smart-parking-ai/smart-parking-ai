"""v3 단계 1(R1·R3): LLM 호출 제안과 평가 파일 이름의 회귀 검사."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, parser, templates as T
from chatbot.adapters import CachedAdapter, MockAdapter


class AdminSuggestionTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(e, 'data', CachedAdapter(MockAdapter())),
                  patch.object(config, 'LLM_ENGINE', 'langchain'),
                  patch('chatbot.llm_router.route', return_value={'intent':'call_admin','params':{}})):
            p.start(); self.addCleanup(p.stop)
        e.reset_all()

    def suggest(self, sid='s', **kwargs):
        return e.handle(sid, text='뿌잉', **kwargs)

    def test_llm_proposes_call_without_receipt_or_pending_action(self):
        got=self.suggest()
        self.assertEqual(got['text'],T.SUGGEST_ADMIN_CALL)
        self.assertEqual(got['meta']['source'],'llm')
        self.assertEqual(got['flow'],'idle')
        self.assertIsNone(got['confirmation_token'])
        self.assertIsNone(got['card'])
        self.assertEqual(e.admin_calls(),[])
        self.assertEqual(got['chips'][0],{'id':'call_admin','label':'관리자 호출'})
        self.assertLessEqual(len(got['chips']),4)
        self.assertLessEqual(len(got['text'].splitlines()),2)

    def test_call_chip_registers_once_and_uses_existing_receipt(self):
        self.suggest()
        first=e.handle('s',chip_id='call_admin',request_id='explicit-click')
        replay=e.handle('s',chip_id='call_admin',request_id='explicit-click')
        self.assertEqual(len(e.admin_calls()),1)
        self.assertEqual(first['meta']['source'],'chip')
        self.assertEqual(first['card']['type'],'admin_call')
        self.assertEqual(first['card'],replay['card'])
        self.assertTrue(replay['meta']['replayed'])

    def test_rule_request_still_registers_immediately(self):
        got=e.handle('s',text='관리자 불러줘')
        self.assertEqual(got['meta']['source'],'rule')
        self.assertEqual(got['card']['type'],'admin_call')
        self.assertEqual(len(e.admin_calls()),1)

    def test_chip_without_proposal_still_registers_immediately(self):
        got=e.handle('s',chip_id='call_admin')
        self.assertEqual(got['card']['type'],'admin_call')
        self.assertEqual(len(e.admin_calls()),1)

    def test_direct_classifier_uses_same_suggestion_guard(self):
        with patch.object(config,'LLM_ENGINE','direct'), patch('chatbot.llm.classify',return_value={'intent':'call_admin','params':{}}):
            got=self.suggest()
        self.assertEqual(e.admin_calls(),[])
        self.assertEqual(got['chips'][0]['id'],'call_admin')

    def test_yes_does_not_approve_a_model_suggestion(self):
        self.suggest()
        got=e.handle('s',text='네')
        self.assertEqual(e.admin_calls(),[])
        self.assertEqual(got['text'],T.NOTHING_TO_CONFIRM)

    def test_suggestion_cancels_old_payment_confirmation(self):
        e.handle('s',text='17번 결제')
        quote=e.handle('s',text='네')
        self.assertEqual(quote['flow'],'confirm_action')
        got=self.suggest()
        self.assertEqual(got['flow'],'idle')
        e.handle('s',chip_id='confirm_yes',confirmation_token=quote['confirmation_token'])
        self.assertFalse(e.data.get_fee('17')['paid'])
        self.assertEqual(e.admin_calls(),[])

    def test_cancel_after_suggestion_has_no_call(self):
        self.suggest()
        e.handle('s',text='관리자 부르지 마세요')
        self.assertEqual(e.admin_calls(),[])

    def test_replayed_suggestion_preserves_call_chip_without_registering(self):
        self.suggest(request_id='suggestion')
        got=self.suggest(request_id='suggestion')
        self.assertTrue(got['meta']['replayed'])
        self.assertEqual(got['chips'][0]['id'],'call_admin')
        self.assertEqual(e.admin_calls(),[])

    def test_replayed_suggestion_does_not_hide_new_payment_confirmation(self):
        self.suggest(request_id='suggestion')
        e.handle('s',text='17번 결제')
        quote=e.handle('s',text='네')
        got=self.suggest(request_id='suggestion')
        self.assertEqual(got['confirmation_token'],quote['confirmation_token'])
        self.assertEqual([c['id'] for c in got['chips']],['confirm_yes','confirm_no'])
        self.assertEqual(e.admin_calls(),[])

    def test_suggestion_has_appropriate_chips_for_selected_vehicle_and_lv1(self):
        for sid,selected,level in (('normal',None,0),('parked','17',0),('warning','17',1)):
            with self.subTest(sid=sid):
                e.get_session(sid).car_id=selected
                e.data.set_alert(level,'A',6)
                got=self.suggest(sid)
                ids=[c['id'] for c in got['chips']]
                self.assertEqual(ids[0],'call_admin')
                self.assertEqual(len(ids),len(set(ids)))
                self.assertLessEqual(len(ids),4)
                self.assertIn('alert_where' if level else 'fee' if selected else 'empty_all',ids)
        self.assertEqual(e.admin_calls(),[])

    def test_lv2_bypasses_llm_but_explicit_rule_and_chip_calls_work(self):
        e.data.set_alert(2,'A',6)
        with patch('chatbot.llm_router.route') as route:
            self.suggest()
            route.assert_not_called()
        self.assertEqual(e.admin_calls(),[])
        for sid,kwargs in (('rule',{'text':'관리자 불러줘'}),('chip',{'chip_id':'call_admin'})):
            got=e.handle(sid,**kwargs)
            self.assertEqual(got['card']['type'],'admin_call')
            self.assertIsNotNone(got['safety'])
        self.assertEqual(len(e.admin_calls()),2)


class EvaluationFilenameTests(unittest.TestCase):
    def test_glob_selects_exactly_seven_existing_question_sets(self):
        directory=Path(__file__).parent
        files=sorted(p for p in directory.glob('questions*.csv') if p.name != 'questions_rehearsal.csv')
        self.assertEqual(len(files),7)
        with_rows=[]
        for file in files:
            with file.open(encoding='utf-8') as stream:with_rows.extend(csv.DictReader(stream))
        self.assertEqual(len(with_rows),237)
        self.assertFalse((directory/'questions_llm.csv').exists())
        with (directory/'llm_eval_questions.csv').open(encoding='utf-8') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),35)
        self.assertTrue(all(parser.parse_rules(row['question']).intent=='unknown' for row in rows))

    def test_comparison_script_resolves_renamed_default_and_explicit_file(self):
        directory=Path(__file__).resolve().parent
        env={**os.environ,'PYTHONIOENCODING':'utf-8','LLM_PROVIDER':'none'}
        for args in ([],[str(directory/'llm_eval_questions.csv')]):
            with self.subTest(args=args):
                got=subprocess.run([sys.executable,str(directory/'run_llm_compare.py'),*args,'--compare'],
                    env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=10,check=True)
                report=json.loads(got.stdout)
                self.assertEqual(report['rules']['total'],35)
                self.assertEqual(report['rules']['correct'],5)
                self.assertIn('skipped',report['hybrid'])


if __name__=='__main__':unittest.main()
