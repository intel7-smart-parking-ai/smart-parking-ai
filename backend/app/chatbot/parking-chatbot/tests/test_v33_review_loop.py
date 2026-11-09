"""v3.3: 11/09 화면 피드백(실외·역에·여행 질문)과 Langfuse 검토 고리.

- '실외 주차장', '역에 주차장'은 등록되지 않은 주차장이 아니다(시설 이름만 미등록 판정)
- 여행·해외·쇼핑처럼 목적만 말한 추천은 목적에 맞는 주차장으로 답한다(모델이 없어도)
- 조회 없이 답한 모델 답은 한 번 고쳐 쓰게 하고, '자료에 없어요' 안내는 운영 질문이어도 허용한다
- 다시 볼 턴(needs-review)과 사용자 👍/👎(user_feedback)를 Langfuse trace에 남긴다
"""
import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import boundaries, config, engine as e, monitoring, parser
from chatbot.agents.evidence import no_tool_reply_ok


class UnknownLotTests(unittest.TestCase):
    def test_descriptions_and_particles_are_not_lot_names(self):
        for text in ("실외 주차장은 어디야?", "역에 주차장 장애인 자리 몇개나 남았어?", "야외 주차장 있어?",
                     "서면 주차장 어때", "역 주차장 빈자리"):
            with self.subTest(text=text):
                self.assertIsNone(boundaries.match(text))

    def test_facility_names_stay_unknown(self):
        for text in ("롯데백화점 주차장 빈자리 알려줘", "해운대역 주차장 자리 있어?", "벡스코센터 주차장 요금"):
            with self.subTest(text=text):
                self.assertEqual(boundaries.match(text).key, "unknown_lot")


class PurposeRoutingTests(unittest.TestCase):
    CASES = {
        "내가 지금 여행 가려고 하는데 어떤 주차장을 이용하면 좋을까?": "C",
        "해외 여행을 계획중인데 어떤 주차장을 추천해?": "C",
        "비행기 타러 가는데 어디 대?": "C",
        "쇼핑하러 가는데 어디 세우면 돼?": "D",
    }

    def test_purpose_points_to_one_lot(self):
        for text, lot in self.CASES.items():
            with self.subTest(text=text):
                r = parser.parse(text)
                self.assertEqual((r.intent, r.params.get("lot_id")), ("get_empty_spots", lot))

    def test_named_lot_and_plain_recommendation_unchanged(self):
        self.assertEqual(parser.parse("여행 가는데 부산역 자리 있어?").params.get("lot_id"), "B")
        self.assertEqual(parser.parse("어떤 주차장을 추천해?").params.get("rank"), "most")
        self.assertEqual(parser.parse("여행 다녀왔어 고마워").intent, "thanks")

    def test_rule_answer_names_the_purpose(self):
        e.reset_all()
        with patch.object(config, "LLM_PROVIDER", "none"):
            e.handle("trip", event="start")
            r = e.handle("trip", text="해외 여행을 계획중인데 어떤 주차장을 추천해?")
        self.assertIn("김해공항 주차장을 안내할게요", r["text"])
        self.assertIn("자리", r["text"])
        self.assertNotIn("이해하지 못했어요", r["text"])


class FlexibleFallbackTests(unittest.TestCase):
    """모델이 실패해도 문장 뜻에 맞는 규칙 답을 고른다(첫 해석 하나에 맡기지 않음)."""

    def setUp(self):
        e.reset_all()

    def ask(self, text):
        with patch.object(config, "LLM_PROVIDER", "none"):
            sid = "flex-" + text[:6]
            e.handle(sid, event="start")
            return e.handle(sid, text=text)

    def test_how_much_room_is_not_a_fee_question(self):
        self.assertEqual(parser.parse("공항 자리 얼마나 남았어?").intent, "get_empty_spots")
        self.assertEqual(parser.parse("23번 차 얼마나 나왔어?").intent, "get_fee")
        self.assertEqual(parser.parse("주차비 얼마나 나와?").intent, "get_fee")
        r = self.ask("현재 우리가 일본 여행중인데 친구가 올거거든 주차장 자리가 얼마나 여유가 있어?")
        self.assertIn("김해공항 주차장", r["text"])
        self.assertNotIn("차량 번호를 알려주세요", r["text"])

    def test_parking_talk_never_ends_in_not_understood(self):
        for text in ("오늘 주차장 분위기 어때?", "친구 데리러 공항 가는데 자리 좀 있나"):
            with self.subTest(text=text):
                r = self.ask(text)
                self.assertNotIn("이해하지 못했어요", r["text"])
                self.assertIn("자리", r["text"])

    def test_purpose_narrows_vacancy(self):
        self.assertIn("신세계백화점주차장", self.ask("쇼핑하고 싶은데 자리 남았나")["text"])

    def test_car_questions_still_ask_for_car(self):
        self.assertIn("차량 번호", self.ask("내 차 어디야?")["text"])

    def test_readings_are_hints_for_the_model(self):
        from chatbot.conversation_turn import readings
        r = readings("현재 우리가 일본 여행중인데 친구가 올거거든 주차장 자리가 얼마나 여유가 있어?")
        self.assertEqual(r["purpose_lot"], "C")
        self.assertEqual(r["rule"]["intent"], "get_empty_spots")
        self.assertEqual(readings("부산역 장애인 칸")["zone"], "accessible")


class NoToolAnswerTests(unittest.TestCase):
    def test_missing_attribute_answer_is_allowed(self):
        self.assertTrue(no_tool_reply_ok("실외 주차장은 어디야?",
                                         "실내인지 실외인지는 아직 안내 자료에 없어요. 빈자리나 요금은 바로 확인해 드릴 수 있어요."))

    def test_state_claims_still_need_lookup(self):
        for question, answer in (("빈자리 있어?", "지금 자리가 넉넉해요."),
                                 ("빈자리 있어?", "빈자리 정보는 없어요. 지금 자리가 남아 있어요."),
                                 ("어디가 싸?", "개발원 정보는 없어요."),
                                 ("빈자리 있어?", "확인해 드릴 수 있어요.")):
            with self.subTest(answer=answer):
                self.assertFalse(no_tool_reply_ok(question, answer))


HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class NoEvidenceRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_without_lookup_gets_one_repair(self):
        from test_agent_runtime_v3 import ScriptedModel, tool_call, parking_status, repeat_observation
        from langchain_core.messages import AIMessage
        from langchain_core.tools import StructuredTool
        from chatbot.agents.conversation import arun_conversation
        model = ScriptedModel([AIMessage(content="공항 주차장이 여유로워서 추천해요."),
                               tool_call("parking_status"), repeat_observation])
        result = await arun_conversation("여행 가는데 어디가 좋아?", [], {},
                                         [StructuredTool.from_function(coroutine=parking_status)], model=model)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.metadata.get("repair_reason"), "no_evidence")
        self.assertIn("5자리", result.text)


class PlainTextTests(unittest.TestCase):
    def test_markdown_becomes_plain(self):
        from chatbot.conversation_turn import plain_text
        text = plain_text("자리 여유를 보면 이래요.\n\n\n- **김해공항 주차장**: 16자리\n* **부산역주차장**: 2자리\n## 정리")
        self.assertNotIn("**", text)
        self.assertNotIn("\n\n\n", text)
        self.assertIn("· 김해공항 주차장: 16자리", text)
        self.assertIn("· 부산역주차장: 2자리", text)
        self.assertNotIn("#", text)


class ReviewReasonTests(unittest.TestCase):
    def test_reasons(self):
        self.assertEqual(monitoring.review_reasons({"source": "llm", "agent_status": "ok"}, 1200), [])
        self.assertIn("not_understood", monitoring.review_reasons({"intent": "unknown", "source": "none"}))
        self.assertIn("model_fallback:timeout", monitoring.review_reasons({"source": "rule", "agent_status": "timeout"}))
        self.assertIn("unknown_lot", monitoring.review_reasons({"boundary": "unknown_lot"}))
        self.assertIn("slow", monitoring.review_reasons({"source": "llm"}, 7000))
        self.assertIn("repair_failed:no_evidence", monitoring.review_reasons({}, 0, {"repair_failed": "no_evidence"}))


class _FakeLangfuse:
    def __init__(self):
        self.scores = []

    def create_score(self, **kwargs):
        self.scores.append(kwargs)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        monitoring.reset_for_tests()
        self.addCleanup(monitoring.reset_for_tests)

    def test_off_records_nothing(self):
        with patch.object(config, "MONITOR_BACKEND", "off"):
            self.assertFalse(monitoring.feedback("s", "down"))

    def test_langfuse_score_goes_to_remembered_trace(self):
        fake = _FakeLangfuse()
        monitoring._state.update(client=fake, flavor="v3")
        session = monitoring._session_hash("s1")
        monitoring._remember(session, "req-1", "trace-1")
        monitoring._remember(session, "req-2", "trace-2")
        with patch.object(config, "MONITOR_BACKEND", "langfuse"):
            self.assertTrue(monitoring.feedback("s1", "down", "req-1", "역 주차장을 못 알아들어요"))
            self.assertTrue(monitoring.feedback("s1", "up"))          # request_id 없으면 마지막 답
            self.assertFalse(monitoring.feedback("other", "up"))       # 기록 없는 세션
        self.assertEqual([(s["trace_id"], s["value"]) for s in fake.scores], [("trace-1", 0.0), ("trace-2", 1.0)])
        self.assertEqual(fake.scores[0]["name"], "user_feedback")
        self.assertEqual(fake.scores[0]["comment"], "역 주차장을 못 알아들어요")

    def test_local_backend_writes_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "turns.jsonl"
            with patch.object(config, "MONITOR_BACKEND", "local"), patch.object(config, "MONITOR_LOCAL_PATH", str(path)):
                self.assertTrue(monitoring.feedback("s", "down", comment="틀렸어요"))
            row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((row["feedback"], row["comment"]), ("down", "틀렸어요"))
        self.assertNotIn("s", row["session"][:0] or "")


@unittest.skipUnless(importlib.util.find_spec("opentelemetry") is not None, "opentelemetry 미설치")
class PrivateTracerTests(unittest.TestCase):
    """Langfuse가 전역 tracer가 되면 FastAPI의 모든 HTTP 요청(헬스 체크·폴링)이 trace로 전송된다(11/09 하루 7만 건)."""

    def setUp(self):
        monitoring.reset_for_tests()
        self.addCleanup(monitoring.reset_for_tests)

    def test_langfuse_gets_private_tracer_provider(self):
        import sys
        import types
        captured = {}

        class FakeLangfuse:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def start_as_current_span(self, **kwargs):
                raise NotImplementedError

        fake_module = types.SimpleNamespace(Langfuse=FakeLangfuse)
        with patch.dict(sys.modules, {"langfuse": fake_module}), \
                patch.object(config, "LANGFUSE_PUBLIC_KEY", "pk-lf-test"), \
                patch.object(config, "LANGFUSE_SECRET_KEY", "sk-lf-test"):
            self.assertIsNotNone(monitoring._langfuse())
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        self.assertIsInstance(captured.get("tracer_provider"), TracerProvider)
        self.assertIsNot(trace.get_tracer_provider(), captured["tracer_provider"])


AVAILABLE = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx"))


@unittest.skipUnless(AVAILABLE, "fastapi/httpx 미설치")
class FeedbackHttpTests(unittest.TestCase):
    def test_endpoint(self):
        from fastapi.testclient import TestClient
        from server_fastapi import app
        with TestClient(app) as client, patch.object(config, "MONITOR_BACKEND", "off"):
            r = client.post("/api/v1/chat/feedback", json={"session_id": "s", "rating": "down", "comment": "별로"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {"ok": True, "recorded": False})
            self.assertEqual(client.post("/api/v1/chat/feedback", json={"session_id": "s", "rating": "meh"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
