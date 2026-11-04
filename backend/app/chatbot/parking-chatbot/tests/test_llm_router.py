"""유료 API 호출 없이 라우터·시간 제한·대화 안전 경계를 검사한다."""
import importlib.util
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import patch

from chatbot import config, engine as e, parser, llm_router as r
from chatbot.adapters import CachedAdapter, MockAdapter, request_deadline


def message(name='get_fee', args=None):
    return SimpleNamespace(tool_calls=[{'name': name, 'args': args if args is not None else {}}])


class Model:
    def __init__(self, response=None):
        self.response = response or message()
        self.calls = 0
        self.tools = None
    def bind_tools(self, tools):
        self.tools = tools
        return self
    def invoke(self, messages):
        self.calls += 1
        return self.response


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(config, 'LLM_ENGINE', 'langchain'),
                        patch.object(r, '_settings', return_value=(('openai', 'fake', .1, 'not-a-key'), None)),
                        patch.object(e, 'data', CachedAdapter(MockAdapter()))]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        e.reset_all()
        with r._model_lock:
            self.assertFalse(r._initializing)
            r._model = r._model_key = r._failure = None
            r._retry_at = 0
        with r._stats_lock:
            self.assertEqual(r._inflight, 0)
            for k in r._counts: r._counts[k] = 0
        self.addCleanup(config.catalog.reset)

    def ready(self, model=None):
        model = model or Model()
        with patch.object(r, '_create_model', return_value=model):
            r.warmup()
            limit = time.monotonic() + 2
            while r.stats()['preparing'] and time.monotonic() < limit: time.sleep(.001)
        self.assertTrue(r.stats()['ready'])
        return model

    def test_tools_match_allowed_intents_and_have_examples(self):
        tools = r.tool_schemas()
        self.assertEqual({t['function']['name'] for t in tools}, parser.KNOWN_INTENTS - {'confirm_yes','confirm_no'})
        self.assertEqual(len(tools), 22)  # v2.9: exit_help(교체) + get_violations(추가)
        self.assertTrue(all('예:' in t['function']['description'] for t in tools))

    def test_catalog_changes_regenerate_tool_enums(self):
        config.catalog.adopt({'East': {'total': 8, 'empty': 2}})
        schema = r.tool_schemas()[0]['function']['parameters']
        self.assertEqual(schema['properties']['lot_id']['enum'], ['East'])
        self.assertEqual(schema['properties']['car_id']['enum'] if 'car_id' in schema['properties'] else [], [])

    def test_normal_tool_call_through_real_parser(self):
        m = self.ready(Model(message('get_fee', {'car_id':'17'})))
        got = parser.parse('자동차 사용료를 산출해 줘')
        self.assertEqual((got.intent,got.params,got.source), ('get_fee',{'car_id':'17'},'llm'))
        self.assertEqual(m.calls, 1)

    def test_rules_and_buttons_never_call_router(self):
        self.ready()
        with patch.object(r,'route') as call:
            parser.parse('요금 얼마야?')
            e.handle('s',chip_id='help')
            parser.parse('관리자 말고 뿌잉')
            call.assert_not_called()

    def test_lv2_skips_router_and_direct_classifier(self):
        e.data.set_alert(2,'A',6)
        for mode in ('langchain','direct'):
            with patch.object(config,'LLM_ENGINE',mode), patch.object(r,'route') as route, patch('chatbot.llm.classify') as direct:
                got = e.handle(mode,text='자동차 사용료를 산출해 줘')
                route.assert_not_called(); direct.assert_not_called()
                self.assertIsNotNone(got['safety'])

    def test_alarm_escalating_during_classification_discards_choice(self):
        model=self.ready();entered,release=threading.Event(),threading.Event()
        def delayed(_):entered.set();release.wait(2);return message('call_admin')
        with patch.object(model,'invoke',side_effect=delayed):
            with ThreadPoolExecutor(max_workers=1) as pool:
                task=pool.submit(e.handle,'s',text='뿌잉')
                try:
                    self.assertTrue(entered.wait(1))
                    e.data.set_alert(2,'A',6);e.current_alert()
                finally:release.set()
                response=task.result(2)
        self.assertFalse(e.ADMIN_CALLS)
        self.assertIsNotNone(response['safety'])
        self.assertNotEqual(response['meta']['source'],'llm')

    def test_catalog_change_discards_old_lot_result(self):
        model=self.ready()
        def changed(_):
            config.catalog.adopt({'East': {'total': 8, 'empty': 2}})
            return message('get_empty_spots',{'lot_id':'A'})
        with patch.object(model,'invoke',side_effect=changed):self.assertIsNone(r.route('뿌잉'))
        self.assertEqual(r.stats()['last_reason'],'invalid_params')

    def test_model_settings_change_while_invoking_discards_result(self):
        model=self.ready()
        with patch.object(r,'_settings',side_effect=[(('openai','fake',.1,'not-a-key'),None),
             (('openai','fake',.1,'not-a-key'),None),(('openai','new',.1,'not-a-key'),None)]):
            self.assertIsNone(r.route('뿌잉'))
        self.assertEqual(r.stats()['last_reason'],'invalid_params')

    def test_first_tool_only_and_no_model_text_execution(self):
        response = message('help'); response.content='요금 0원, 결제 완료'
        response.tool_calls.append({'name':'call_admin','args':{}})
        self.ready(Model(response))
        result = e.handle('s',text='뿌잉')
        self.assertEqual(result['meta']['intent'],'help')
        self.assertNotIn('결제 완료',result['text']); self.assertFalse(e.ADMIN_CALLS)

    def test_rejects_invalid_tool_payloads(self):
        cases = [(SimpleNamespace(tool_calls=[]),'no_tool_call'),
                 (message('confirm_yes'),'invalid_intent'), (message('delete_everything'),'invalid_intent'),
                 (message('get_fee',[]),'invalid_params'),
                 (message('get_empty_spots',{'lot_id':'Z'}),'invalid_params'),
                 (message('get_empty_spots',{'zone':'bogus'}),'invalid_params'),
                 (message('get_fee',{'car_id':'7'}),'invalid_params'),  # 두 자리 규칙 밖
                 (message('get_fee',{'car_id':7}),'invalid_params'),
                 (message('get_empty_spots',{'vacancy_notice':True}),'invalid_params'),
                 (message('set_destination'),'invalid_params'),(message('change_vehicle'),'invalid_params')]
        model = self.ready()
        for payload, reason in cases:
            with self.subTest(reason=reason,payload=payload):
                model.response = payload
                self.assertIsNone(r.route('뿌잉'))
                self.assertEqual(r.stats()['last_reason'],reason)
                self.assertEqual(parser.parse('뿌잉').intent,'unknown')

    def test_zone_is_validated_and_preserved(self):
        zone = config.catalog.zones()[0]['key']
        self.ready(Model(message('get_empty_spots',{'zone':zone})))
        self.assertEqual(parser.parse('뿌잉').params,{'zone':zone})

    def test_user_vehicle_wins_over_model(self):
        self.ready(Model(message('get_fee',{'car_id':'18'})))
        self.assertEqual(parser.parse('17번 차 뿌잉').params['car_id'],'17')

    def test_actions_cannot_invent_vehicle_number(self):
        m = self.ready()
        for intent in ('pay_fee','call_admin'):
            m.response = message(intent,{'car_id':'18'})
            self.assertEqual(r.route('뿌잉'),{'intent':intent,'params':{}})

    def test_payment_tool_still_requires_vehicle_and_amount_confirmations(self):
        self.ready(Model(message('pay_fee')))
        got = e.handle('s',text='뿌잉')
        self.assertEqual(got['flow'],'ask_vehicle')
        e.handle('s',text='17'); got=e.handle('s',text='네')
        self.assertEqual(got['flow'],'confirm_action')
        self.assertFalse(e.data.get_fee('17')['paid'])
        e.handle('s',chip_id='confirm_yes',confirmation_token=got['confirmation_token'])
        self.assertTrue(e.data.get_fee('17')['paid'])

    def test_admin_tool_registers_once_after_call_chip(self):
        self.ready(Model(message('call_admin')))
        first=e.handle('s',text='뿌잉',request_id='same')
        second=e.handle('s',text='뿌잉',request_id='same')
        self.assertEqual(len(e.ADMIN_CALLS),0)
        self.assertIn('call_admin',[c['id'] for c in first['chips']])
        e.handle('s',chip_id='call_admin',request_id='call-chip')
        e.handle('s',chip_id='call_admin',request_id='call-chip')
        self.assertEqual(first['flow'],'idle');self.assertEqual(len(e.ADMIN_CALLS),1)
        self.assertTrue(second['meta']['replayed'])

    def test_reuses_model_without_blocking_stats(self):
        model = self.ready()
        with patch.object(r,'_create_model') as create:
            for _ in range(5): self.assertIsNotNone(r.route('뿌잉'))
            create.assert_not_called()
        self.assertEqual(model.calls,5)
        self.assertEqual(e.stats()['llm']['counts']['ok'],5)

    def test_slow_initialization_is_single_background_task(self):
        entered, release = threading.Event(), threading.Event()
        def prepare(_): entered.set();release.wait(2);return Model()
        with patch.object(r,'_create_model',side_effect=prepare) as create:
            started=time.monotonic()
            try:
                r.warmup();self.assertTrue(entered.wait(.5))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results=list(pool.map(r.route,['뿌잉']*20))
                self.assertTrue(all(x is None for x in results))
                self.assertTrue(r.stats()['preparing']);self.assertLess(time.monotonic()-started,.5)
                self.assertEqual(create.call_count,1)
            finally:
                release.set()
                while r.stats()['preparing']:time.sleep(.001)

    def test_missing_package_falls_back_and_logs_once(self):
        with patch.object(r,'_create_model',side_effect=ModuleNotFoundError('missing')):
            r.warmup()
            while r.stats()['preparing']:time.sleep(.001)
            self.assertIsNone(r.route('뿌잉'))
            self.assertEqual(r.stats()['last_reason'],'not_installed')
        self.assertIn('text',e.handle('s',chip_id='help'))

    def test_disabled_and_missing_key_counters(self):
        for reason in ('disabled','missing_key'):
            with patch.object(r,'_settings',return_value=(None,reason)):
                self.assertIsNone(r.route('뿌잉'))
            self.assertEqual(r.stats()['counts'][reason],1)

    def test_api_error_never_logs_exception_or_user_text(self):
        model=self.ready()
        with patch.object(model,'invoke',side_effect=RuntimeError('private-user-text-secret')):
            self.assertIsNone(r.route('private-user-text-secret'))
        self.assertEqual(r.stats()['last_reason'],'api_error')
        self.assertNotIn('private-user-text-secret',str(r.stats()))

    def test_timeout_discards_late_result_and_bounds_concurrency(self):
        model=self.ready();release=threading.Event()
        def delayed(_): release.wait(3);return message('call_admin')
        with patch.object(model,'invoke',side_effect=delayed),patch.object(config,'LLM_TIMEOUT_SEC',.04):
            try:
                started=time.monotonic()
                self.assertEqual(parser.parse('뿌잉').intent,'unknown')
                self.assertIsNone(r.route('뿌잉'))
                self.assertIsNone(r.route('뿌잉'))
                self.assertEqual(r.stats()['last_reason'],'busy')
                self.assertEqual(r.stats()['inflight'],2)
                self.assertLess(time.monotonic()-started,.4)
            finally:
                release.set()
                while r.stats()['inflight']:time.sleep(.001)
        self.assertEqual(r.stats()['counts']['ok'],0);self.assertFalse(e.ADMIN_CALLS)
        self.assertIsNotNone(r.route('뿌잉'))

    def test_default_three_second_wall_clock_limit(self):
        model=self.ready();release=threading.Event()
        def delayed(_):release.wait(5);return message('get_fee')
        with patch.object(model,'invoke',side_effect=delayed),patch.object(config,'LLM_TIMEOUT_SEC',3):
            try:
                started=time.monotonic()
                self.assertIsNone(r.route('뿌잉'))
                elapsed=time.monotonic()-started
                self.assertGreaterEqual(elapsed,2.8)
                self.assertLess(elapsed,3.7)
                self.assertEqual(r.stats()['last_reason'],'timeout')
            finally:
                release.set()
                while r.stats()['inflight']:time.sleep(.001)

    def test_bind_tools_delay_is_included_in_deadline(self):
        m=self.ready();release=threading.Event()
        def bind(_):release.wait(2);return m
        with patch.object(m,'bind_tools',side_effect=bind),patch.object(config,'LLM_TIMEOUT_SEC',.03):
            try:self.assertIsNone(r.route('뿌잉'));self.assertEqual(r.stats()['last_reason'],'timeout')
            finally:
                release.set()
                while r.stats()['inflight']:time.sleep(.001)

    def test_exhausted_request_budget_never_invokes(self):
        model=self.ready();token=request_deadline.set(time.monotonic()-1)
        try:self.assertIsNone(r.route('뿌잉'))
        finally:request_deadline.reset(token)
        self.assertEqual(model.calls,0)

    def test_input_limit(self):
        self.assertIsNone(r.route('x'*201));self.assertEqual(r.stats()['last_reason'],'invalid_params')

    def test_unknown_intent_is_not_a_business_action(self):
        self.ready(Model(message('unknown')))
        self.assertEqual(parser.parse('뿌잉').intent,'unknown')
        self.assertFalse(e.ADMIN_CALLS)

    @unittest.skipUnless(importlib.util.find_spec('langchain_openai') and importlib.util.find_spec('langchain_anthropic'), '선택 제공자 패키지 미설치')
    def test_real_provider_construction_and_binding_without_network(self):
        from langchain_openai import ChatOpenAI
        from langchain_anthropic import ChatAnthropic
        # 이 검사만 실행 환경의 SOCKS 프록시를 제외한다. 실제 통신은 하지 않는다.
        clean={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')}
        clean.update(OPENAI_API_KEY='test-placeholder',ANTHROPIC_API_KEY='test-placeholder')
        with patch.dict(os.environ,clean,clear=True):
            for provider, cls in (('openai',ChatOpenAI),('anthropic',ChatAnthropic)):
                model=r._create_model((provider,'constructor-only',3,'digest'))
                self.assertIsInstance(model,cls)
                self.assertEqual(model.max_retries,0)
                self.assertEqual(len(model.bind_tools(r.tool_schemas()).kwargs['tools']),22)

    @unittest.skipUnless(importlib.util.find_spec('langchain_core'), '선택 LangChain 미설치')
    def test_real_langchain_fake_chat_model_and_tool_messages(self):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage
        class FakeTools(GenericFakeChatModel):
            def bind_tools(self,tools,**kwargs):return self.bind(tools=tools)
        model=FakeTools(messages=iter([AIMessage(content='',tool_calls=[{'name':'get_fee','args':{'car_id':'17'},'id':'test'}])]))
        self.ready(model)
        self.assertEqual(r.route('뿌잉'),{'intent':'get_fee','params':{'car_id':'17'}})


if __name__=='__main__':unittest.main()
