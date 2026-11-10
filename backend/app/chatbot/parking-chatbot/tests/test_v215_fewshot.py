"""Use a fake model and real optional message objects; never contact a provider."""
import importlib.util
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, llm_router as router, llm_limits
from chatbot.adapters import CachedAdapter, MockAdapter
from chatbot.llm_examples import EXAMPLES

HAS_MESSAGES = importlib.util.find_spec("langchain_core") is not None


class RecordingModel:
    def __init__(self, intent="get_fee"):
        self.intent, self.calls, self.messages = intent, 0, None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        return SimpleNamespace(tool_calls=[{"name":self.intent,"args":{}}])


class FewshotTests(unittest.TestCase):
    def setUp(self):
        self.model = RecordingModel()
        for item in (patch.object(config,"LLM_FEWSHOT",False), patch.object(config,"LLM_ENGINE","langchain"),
                     patch.object(router,"_settings",return_value=(("openai","fake",1,"digest"),None)),
                     patch.object(e,"data",CachedAdapter(MockAdapter())),
                     patch.object(llm_limits,"limits",llm_limits.RouteLimits())):
            item.start(); self.addCleanup(item.stop)
        e.reset_all()
        with router._model_lock:
            self.assertFalse(router._initializing)
            router._model = router._model_key = router._failure = None
            router._retry_at = 0
        with patch.object(router,"_create_model",return_value=self.model):
            router.warmup()
            deadline = time.monotonic()+2
            while router.stats()["preparing"] and time.monotonic()<deadline:
                time.sleep(.001)
        self.assertTrue(router.stats()["ready"])

    def test_default_switch_is_off_and_off_messages_stay_two(self):
        self.assertEqual(config.getenv("LLM_FEWSHOT","0"),"0")
        router.route("합성 분류 요청")
        self.assertEqual(len(self.model.messages),2)
        self.assertEqual(self.model.messages[-1],("human","합성 분류 요청"))
        self.assertEqual(self.model.messages[0][0],"system")

    @unittest.skipUnless(HAS_MESSAGES,"선택 LangChain 메시지 패키지 미설치")
    def test_three_valid_example_triples_precede_the_current_user(self):
        from langchain_core.messages import AIMessage,HumanMessage,ToolMessage,convert_to_messages
        config.LLM_FEWSHOT = True
        router.route("현재 요청")
        messages = self.model.messages
        self.assertEqual(len(messages),11)
        self.assertEqual(messages[-1],("human","현재 요청"))
        for i,(text,intent) in enumerate(EXAMPLES):
            human,ai,tool = messages[1+i*3:4+i*3]
            self.assertIsInstance(human,HumanMessage)
            self.assertIsInstance(ai,AIMessage)
            self.assertIsInstance(tool,ToolMessage)
            self.assertEqual(human.content,text)
            self.assertEqual(ai.tool_calls[0]["name"],intent)
            self.assertEqual(ai.tool_calls[0]["args"],{})
            self.assertEqual(ai.tool_calls[0]["id"],tool.tool_call_id)
        self.assertEqual(len(convert_to_messages(messages)),11)
        self.assertEqual(e.admin_calls(),[])

    @unittest.skipUnless(HAS_MESSAGES,"선택 LangChain 메시지 패키지 미설치")
    def test_examples_and_model_choices_are_classification_only(self):
        config.LLM_FEWSHOT = True
        for intent in ("call_admin", "pay_fee"):
            self.model.intent = intent
            with patch.object(e.data,"pay",side_effect=AssertionError("must not pay")):
                result=router.route("합성 요청")
            self.assertEqual(result, {"intent": intent, "params": {}})
        self.assertEqual(e.admin_calls(),[])
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_level_two_does_not_call_the_classifier_even_when_enabled(self):
        config.LLM_FEWSHOT = True
        e.data.set_alert(2,"A",5)
        with patch.object(router,"route",side_effect=AssertionError("must not classify")):
            result=e.handle("alarm",text="합성 요청")
        self.assertIsNotNone(result["safety"])
        self.assertEqual(self.model.calls,0)

    @unittest.skipUnless(HAS_MESSAGES,"선택 LangChain 메시지 패키지 미설치")
    def test_cached_classifications_are_separate_for_each_example_mode(self):
        token=llm_limits.session_id.set("cache-probe")
        try:
            router.route("합성 캐시 요청")
            config.LLM_FEWSHOT=True
            router.route("합성 캐시 요청")
            self.assertEqual(self.model.calls,2)
            config.LLM_FEWSHOT=False
            router.route("합성 캐시 요청")
            self.assertEqual(self.model.calls,2)
            self.assertEqual(router.stats()["last_reason"],"cache_hit")
        finally:
            llm_limits.session_id.reset(token)


if __name__ == "__main__":
    unittest.main()
