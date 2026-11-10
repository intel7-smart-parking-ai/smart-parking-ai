"""Small-pool admission, backend recovery and fake provider cost limits."""
import asyncio
import importlib.util
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chatbot import adapters, agents, config, engine, llm_limits, llm_router
from tests import test_api_contract as wire, test_llm_router as fake


class BreakerTests(unittest.TestCase):
    def test_three_failures_open_and_one_recovery_probe(self):
        api = adapters.ApiAdapter(config.BACKEND_BASE_URL)
        path = config.API_PATHS["lots"]
        clock = SimpleNamespace(monotonic=lambda: 100)
        probe = api._breakers["lookup"]
        with (patch("chatbot.breaker.time", clock),
              patch.object(adapters, "load_json", return_value=(503, {}, {})) as transport):
            for _ in range(3):
                api._request_uncached("GET", path)
            self.assertEqual(api.breaker_stats()["lookup"], {"state": "open", "opened": 1})
            self.assertEqual(api._request_uncached("GET", path)[0], -1)
            self.assertEqual(transport.call_count, 3)
            clock.monotonic = lambda: 111
            token = probe.admit(may_probe=True)
            self.assertIsNotNone(token)
            self.assertIsNone(probe.admit(may_probe=True))
            self.assertIsNone(probe.admit(may_probe=False))
            probe.complete(token, True)
            self.assertEqual(api.breaker_stats()["lookup"]["opened"], 2)
            clock.monotonic = lambda: 122
            transport.return_value = (200, {}, {})
            self.assertEqual(api._request_uncached("GET", path)[0], 200)
            self.assertEqual(api.breaker_stats()["lookup"], {"state": "closed", "opened": 2})

    def test_http_business_and_auth_responses_do_not_open(self):
        api = adapters.ApiAdapter(config.BACKEND_BASE_URL)
        path = config.API_PATHS["alerts"]
        for status in (400, 401, 403, 404, 409):
            with patch.object(adapters, "load_json", return_value=(status, {}, {})) as transport:
                for _ in range(4):
                    self.assertEqual(api._request_uncached("GET", path)[0], status)
                self.assertEqual(transport.call_count, 4)
        self.assertEqual(api.breaker_stats()["alerts"], {"state": "closed", "opened": 0})

    def test_last_lv2_is_preserved_when_alert_group_opens(self):
        api = adapters.ApiAdapter(config.BACKEND_BASE_URL)
        alert = {"alert_id": "breaker", "type": "mock_fire_led", "status": "active", "level": 2,
                 "lot_id": "A", "space_id": "A-02"}
        with patch.object(adapters, "load_json", return_value=(200, {"items": [alert],
                                                       "as_of": adapters.iso(adapters.now())}, {})) as transport:
            self.assertEqual(api.get_alert()["level"], 2)
            transport.side_effect = TimeoutError
            for _ in range(4):
                result = api.get_alert()
                self.assertEqual(result["level"], 2)
                self.assertTrue(result["unavailable"])
            self.assertEqual(transport.call_count, 4)


class PaymentBreakerTests(unittest.TestCase):
    def test_unsent_and_lost_post_have_different_safety_state(self):
        for blocked in (True, False):
            with self.subTest(blocked=blocked):
                fixture = wire.ApiContractTests()
                fixture.setUp()
                try:
                    fixture.session_id = "v212-payment"
                    pending = fixture.ask("11번 결제")
                    original_pay = engine.data.pay
                    original_transport = adapters.load_json
                    attempted = []

                    def transport(request, *args, **kwargs):
                        if request.get_method() == "POST":
                            attempted.append(request.get_method())
                            raise TimeoutError
                        return original_transport(request, *args, **kwargs)

                    def pay(*args, **kwargs):
                        if blocked:
                            breaker = fixture.api._breakers["billing"]
                            for _ in range(config.BACKEND_BREAKER_FAILS):
                                breaker.complete(breaker.admit(may_probe=True), True)
                        return original_pay(*args, **kwargs)

                    with (patch.object(engine.data, "pay", side_effect=pay),
                          patch.object(adapters, "load_json", side_effect=transport)):
                        engine.handle("s", chip_id="confirm_yes",
                                      confirmation_token=pending["confirmation_token"])
                    self.assertEqual(len(attempted), int(not blocked))
                    self.assertEqual(len(engine._uncertain_payments), int(not blocked))
                    self.assertEqual(fixture.posts(), [])
                finally:
                    fixture.tearDown()
                    engine.reset_all()


class LlmLimitTests(unittest.TestCase):
    def setUp(self):
        self.model = fake.Model(fake.message("call_admin"))
        settings = ("openai", "test", 3, "test-key-digest")
        for target, attr, value in (
            (engine, "data", adapters.CachedAdapter(adapters.MockAdapter())),
            (config, "LLM_ENGINE", "langchain"),
            (llm_router, "_settings", lambda: (settings, None)),
            (llm_router, "_model", self.model),
            (llm_router, "_model_key", settings),
            (llm_limits, "limits", llm_limits.RouteLimits()),
        ):
            change = patch.object(target, attr, value)
            change.start()
            self.addCleanup(change.stop)
        engine.reset_all()
        self.addCleanup(engine.reset_all)

    def classify(self, session, text):
        token = llm_limits.session_id.set(session)
        try:
            return llm_router.route(text)
        finally:
            llm_limits.session_id.reset(token)

    def test_legacy_cache_normalizes_text_but_never_executes_admin(self):
        first = self.classify("one", "ＲＥＳＵＬＴ  PLEASE")
        second = self.classify("two", "result please")
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(first, {"intent": "call_admin", "params": {}})
        self.assertEqual(second, first)
        self.assertEqual(engine.ADMIN_CALLS, [])

    def test_failed_classification_is_not_cached(self):
        self.model.response = SimpleNamespace(tool_calls=[])
        self.assertIsNone(self.classify("one", "vague phrase"))
        self.assertIsNone(self.classify("one", "vague phrase"))
        self.assertEqual(self.model.calls, 2)


@unittest.skipUnless(importlib.util.find_spec("langchain"), "optional LangChain not installed")
class AgentLimitTests(unittest.TestCase):
    def setUp(self):
        from langchain_core.messages import AIMessage
        from tests.test_agent_runtime_v3 import ScriptedModel

        # A response without observations is rejected, but the provider attempt still costs quota.
        self.model = ScriptedModel([AIMessage(content="추가 정보가 필요해요.") for _ in range(20)])
        for target, attr, value in (
            (engine, "data", adapters.CachedAdapter(adapters.MockAdapter())),
            (config, "LLM_ENGINE", "langchain"),
            (config, "LLM_PROVIDER", "openrouter"),
            (config, "LLM_MODEL", "test-only"),
            (config, "api_key", lambda _: "test-only"),
            (llm_limits, "limits", llm_limits.RouteLimits()),
            (agents.conversation, "_provider_model", lambda _: self.model),
        ):
            self.enterContext(patch.object(target, attr, value))
        engine.reset_all()
        self.addCleanup(engine.reset_all)

    def test_session_limit_and_fallback(self):
        before = agents.stats()["counts"].get("rate_limited", 0)
        with patch.object(config, "LLM_MAX_PER_SESSION_MIN", 5):
            for i in range(6):
                result = engine.handle("limited", text="뿌잉" + "ㅋ" * i)
        self.assertEqual(len(self.model._received), 5)
        self.assertEqual(result["meta"]["source"], "none")
        self.assertEqual(agents.stats()["counts"]["rate_limited"], before + 1)

    def test_global_daily_and_unlimited_daily(self):
        for global_max, daily_max, expected in ((2, 0, 2), (30, 2, 2), (30, 0, 3)):
            with (patch.object(config, "LLM_MAX_PER_MIN", global_max),
                  patch.object(config, "LLM_MAX_PER_DAY", daily_max),
                  patch.object(llm_limits, "limits", llm_limits.RouteLimits())):
                calls = len(self.model._received)
                for i in range(3):
                    engine.handle(f"limited{i}", text="뿌잉" + "ㅋ" * i)
                self.assertEqual(len(self.model._received) - calls, expected)

    def test_failed_answer_is_not_cached_and_lv2_does_not_invoke(self):
        engine.handle("one", text="vague phrase")
        engine.handle("one", text="vague phrase")
        self.assertEqual(len(self.model._received), 2)
        engine.data.set_alert(2, "A", 6)
        result = engine.handle("one", text="vague phrase")
        self.assertEqual(len(self.model._received), 2)
        self.assertIsNotNone(result["safety"])


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("fastapi", "httpx")),
                     "FastAPI/httpx unavailable")
class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_excess_chat_replies_before_active_work_finishes(self):
        from fastapi import FastAPI
        import httpx
        from chatbot.http_router import router

        app = FastAPI()
        app.include_router(router)
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        handle = engine.handle

        def blocked(*args):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise AssertionError("active chat was not released")
            return handle(*args)

        with (patch.object(config, "EMBEDDED", True),
              patch.object(config, "CHAT_MAX_CONCURRENT", 1),
              patch.object(config, "CHAT_QUEUE_WAIT_SEC", 0.2),   # 11/10: 바로 거절하지 않고 잠깐 줄을 선다 → 그 뒤 '바쁨'
              patch.object(engine, "data", adapters.CachedAdapter(adapters.MockAdapter())),
              patch.object(engine, "handle", side_effect=blocked) as handler):
            engine.reset_all()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                path = "/api/v1/chat"
                first = asyncio.create_task(client.post(path, json={"session_id": "active", "text": "도움"}))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    response = await asyncio.wait_for(client.post(path, json={"session_id": "busy", "text": "도움"}), 1)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["meta"]["busy"])
                    self.assertEqual(handler.call_count, 1)
                    self.assertNotIn("busy", engine.SESSIONS)
                finally:
                    release.set()
                    await first
                    engine.reset_all()
