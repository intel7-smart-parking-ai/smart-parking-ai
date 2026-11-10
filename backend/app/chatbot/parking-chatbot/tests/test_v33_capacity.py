"""구조 점검(11/10 저녁): 여럿이 함께 쓰면 AI 답 대신 규칙 답으로 떨어지던 한도 구조, 근거 검사 위치 어긋남.

부하 실험(가짜 모델 2초): 자리 4·대기 1초 → 동시 6명 중 2명·12명 중 8명 규칙 답, 한 사람 6번째 질문부터 규칙 답.
고친 뒤: 동시 12명 모두 AI 답(가장 느린 답 4.1초), 연속 8질문·12명 36질문 모두 AI 답.
"""
import asyncio
import importlib.util
import json
import os
import threading
import unittest
from unittest.mock import patch

from chatbot import adapters, agents, config, engine as e, llm_limits
from chatbot.agents.evidence import check_answer
from chatbot.tools.read import ReadTools

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None


class EvidencePositionTests(unittest.TestCase):
    """'자료 없음' 문장을 뺀 글로 위치를 재서, 뒤 문장의 '만차'·숫자가 다른 주차장에 묶이던 회귀."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        tools = ReadTools(e.data, e.current_alert)
        self.compare = tools.compare_lots()
        self.rows = {r["lot_id"]: r for r in self.compare["data"]["rows"]}
        self.obs = [{"tool": "search_knowledge", "args": {}, "result": tools.search_knowledge("실외 주차장은 어디야?")},
                    {"tool": "compare_lots", "args": {}, "result": self.compare}]

    def listing(self):
        parts = [f"{r['name']} {'만차' if r['empty'] == 0 else str(r['empty']) + '자리'}" for r in self.rows.values()]
        return "빈자리는 " + ", ".join(parts) + "예요."

    def test_listing_after_no_data_sentence_passes(self):
        self.assertTrue(any(r["empty"] == 0 for r in self.rows.values()), "만차인 주차장이 있어야 의미 있는 검사")
        answer = "실내·실외 정보는 아직 없어요. " + self.listing()
        self.assertIsNone(check_answer(answer, "실외 주차장은 어디야?", self.obs))

    def test_wrong_full_claim_is_still_blocked(self):
        open_lot = next(r for r in self.rows.values() if r["empty"] > 0)
        answer = f"실내·실외 정보는 아직 없어요. {open_lot['name']}은 만차예요."
        self.assertEqual(check_answer(answer, "실외 주차장은 어디야?", self.obs), "contradictory_availability")

    def test_wrong_number_for_lot_is_still_blocked(self):
        a, b = [r for r in self.rows.values() if r["empty"] > 0][:2]
        if a["empty"] == b["empty"]:
            b = dict(b, empty=b["empty"] - 1)   # 구분되도록 조회에 없는 값으로
        answer = f"실내·실외 정보는 아직 없어요. {a['name']}은 {b['empty'] + 1000}자리 비어 있어요."
        self.assertIsNotNone(check_answer(answer, "실외 주차장은 어디야?", self.obs))


class LimitDefaultTests(unittest.TestCase):
    def test_defaults_allow_a_group_demo(self):
        if any(os.getenv(k) for k in ("LLM_MAX_PER_SESSION_MIN", "LLM_MAX_PER_MIN", "LLM_MAX_INFLIGHT",
                                      "CHAT_MAX_CONCURRENT", "LLM_SLOT_WAIT_SEC")):
            self.skipTest("환경 변수로 한도를 지정한 실행")
        self.assertGreaterEqual(config.LLM_MAX_PER_SESSION_MIN, 10)
        self.assertGreaterEqual(config.LLM_MAX_PER_MIN, 60)
        self.assertGreaterEqual(config.LLM_MAX_INFLIGHT, 8)
        self.assertGreaterEqual(config.CHAT_MAX_CONCURRENT, 12)
        self.assertGreaterEqual(config.LLM_SLOT_WAIT_SEC, 3)


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class SlotWaitTests(unittest.TestCase):
    """모델 자리가 다 찼을 때 1초 만에 규칙 답으로 포기하지 않고, 예산 안에서 자리를 기다려 AI 답을 낸다."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        llm_limits.limits.turns.clear()

    def test_waits_for_a_slot_instead_of_rule_answer(self):
        from langchain_core.messages import AIMessage, ToolMessage
        from test_agent_runtime_v3 import ScriptedModel

        def model():
            async def answer(messages):
                await asyncio.sleep(0.6)
                row = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)][0]["data"]["rows"][0]
                return AIMessage(content=f"{row['name']}에는 지금 {row['empty']}자리가 비어 있어요.")
            return ScriptedModel([answer] * 4)

        run = agents.run_conversation
        results = {}

        def work(i):
            results[i] = e.handle(f"slot{i}", text="공항 자리 있어?")["meta"].get("route", "")

        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MAX_INFLIGHT", 2), \
                patch.object(config, "LLM_SLOT_WAIT_SEC", 4.0), \
                patch.object(agents, "run_conversation", side_effect=lambda *a: run(*a, model=model())):
            threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]   # 자리 2개에 6명
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual([r.split(" > ")[-1] for r in results.values()], ["agent:answer"] * 6, results)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("fastapi", "httpx")), "FastAPI/httpx unavailable")
class ChatQueueTests(unittest.IsolatedAsyncioTestCase):
    """동시 대화 자리가 찼을 때 바로 '바쁨'으로 거절하지 않고 잠깐 줄을 서서 답을 받는다."""

    async def test_second_chat_waits_and_gets_answer(self):
        from fastapi import FastAPI
        import httpx
        from chatbot.http_router import router

        app = FastAPI()
        app.include_router(router)
        handle = e.handle
        release = threading.Event()

        def slow(*args):
            if args and args[0] == "first":
                release.wait(0.6)
            return handle(*args)

        with (patch.object(config, "EMBEDDED", True), patch.object(config, "CHAT_MAX_CONCURRENT", 1),
              patch.object(config, "CHAT_QUEUE_WAIT_SEC", 3.0), patch.object(config, "LLM_PROVIDER", "none"),
              patch.object(e, "data", adapters.CachedAdapter(adapters.MockAdapter())),
              patch.object(e, "handle", side_effect=slow)):
            e.reset_all()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = asyncio.create_task(client.post("/api/v1/chat", json={"session_id": "first", "text": "빈자리 있어?"}))
                await asyncio.sleep(0.1)
                second = await client.post("/api/v1/chat", json={"session_id": "second", "text": "빈자리 있어?"})
                await first
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["meta"].get("busy"), second.json()["meta"])
        self.assertIn("자리", second.json()["text"])


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("fastapi", "httpx")), "FastAPI/httpx unavailable")
class LanguageContractTests(unittest.TestCase):
    """계약(API_CONTRACT '챗봇 응답 언어 선택'): language를 생략하면 한국어 응답·기존 필드 구성 유지."""

    def post(self, body):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatbot.http_router import router
        app = FastAPI()
        app.include_router(router)
        e.reset_all()
        with patch.object(config, "LLM_PROVIDER", "none"), TestClient(app) as client:
            return client.post("/api/v1/chat", json=body).json()

    def test_omitted_language_keeps_korean_fields(self):
        r = self.post({"session_id": "lang-a", "text": "Is there parking at Gimhae Airport?"})
        self.assertNotIn("language", r)
        self.assertNotIn("translation_status", r)
        self.assertNotIn("translate_in", r["meta"].get("route", ""))

    def test_guess_is_opt_in(self):
        with patch.object(config, "CHAT_GUESS_LANGUAGE", True):
            r = self.post({"session_id": "lang-b", "text": "Is there parking at Gimhae Airport?"})
        self.assertEqual(r.get("language"), "en")


if __name__ == "__main__":
    unittest.main()
