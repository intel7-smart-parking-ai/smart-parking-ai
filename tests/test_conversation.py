import os
import unittest
from importlib import import_module
from unittest.mock import patch
from backend.app.chatbot import router
from backend.app.chatbot.report_lab import chat_bridge,conversation_llm

engine=import_module('backend.app.chatbot._parking_runtime.engine')
adapters=import_module('backend.app.chatbot._parking_runtime.adapters')
conversation=import_module('backend.app.chatbot._parking_runtime.conversation')


class ConversationTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(engine,'data',adapters.CachedAdapter(adapters.MockAdapter())),
                  patch.object(engine.config,'DATA_SOURCE','mock'),
                  patch.dict(os.environ,{'REPORT_CHAT_ENABLED':'1','REPORT_LANGFUSE':'0','REPORT_OPENROUTER_KEY':'','REPORT_API_BASE':''})):
            p.start();self.addCleanup(p.stop)
        engine.reset_all()

    def test_greeting_thanks_complaint_explain_and_vague_need_no_llm(self):
        with patch.object(conversation_llm,'interpret') as llm,patch.object(chat_bridge,'report') as report:
            for text,category in [('ㅎㅇ','greeting'),('고마워','thanks'),('왜 이래?','complaint'),('뭐지?','explain'),('도와줘','clarify')]:
                response=engine.handle('social',text=text)
                self.assertEqual(response['meta']['conversation_intent'],category)
                self.assertNotIn('잘 이해하지 못했',response['text'])
        llm.assert_not_called();report.assert_not_called()

    def test_social_reply_never_confirms_pending_action(self):
        session=engine.get_session('pending')
        pending=session.begin(engine.Flow.CONFIRM_ACTION,intent='pay_fee',car_id='23')
        with patch.object(engine,'_dispatch') as dispatch:
            response=engine.handle('pending',text='고마워')
        dispatch.assert_not_called()
        self.assertIs(session.pending,pending)
        self.assertEqual(response['meta']['conversation_intent'],'thanks')

    def test_followup_switches_lot_preserving_ev_condition(self):
        scopes=[]
        def report(text,query=None):
            scopes.append((list(query.lots),query.space_type))
            return {'tasks':['spaces'],'cards':[{'title':'현재 빈자리','summary':'조회했어요.','values':{}}],
                    'clarification':None,'summary':'조회했어요.','trace_id':None,'prompt_versions':{},'source':'sample'}
        with patch.object(chat_bridge,'report',side_effect=report):
            engine.handle('followup',text='부산역 전기차 빈자리')
            second=engine.handle('followup',text='그럼 공항은?')
        self.assertEqual(scopes,[(['B'],'ev'),(['C'],'ev')])
        self.assertEqual(second['meta']['conversation_intent'],'followup')

    def test_expired_context_is_not_rewritten(self):
        engine.handle('old',event='start')
        session=engine.get_session('old')
        session.conversation_question='부산역 전기차 빈자리'
        session.conversation_at-=engine.config.QUERY_CONTEXT_TTL_SEC+1
        self.assertIsNone(conversation.common(session,'그럼 공항은?'))

    def test_unknown_expression_uses_bounded_read_only_interpretation(self):
        with patch.dict(os.environ,{'REPORT_OPENROUTER_KEY':'test'}),patch.object(conversation_llm,'interpret',return_value={'intent':'business','task':'spaces','trace_id':'intent-trace'}):
            response=engine.handle('semantic',text='차 놓을 데 찾아볼래')
        self.assertEqual(response['meta']['interpretation_source'],'llm')
        self.assertEqual(response['meta']['intent'],'get_empty_spots')

    def test_llm_timeout_stays_helpful_without_facts(self):
        with patch.dict(os.environ,{'REPORT_OPENROUTER_KEY':'test'}),patch.object(conversation_llm,'interpret',side_effect=TimeoutError):
            response=engine.handle('timeout',text='어쩌구저쩌구')
        self.assertIn('어떤 도움이',response['text'])
        self.assertNotIn('원',response['text'])

    def test_alert_safety_precedes_social_reply(self):
        engine.data.set_alert(2,'A',1)
        response=engine.handle('safety',text='ㅎㅇ')
        self.assertTrue(response['safety'])
        self.assertIn('경보',response['text'])

if __name__=='__main__':
    unittest.main()
