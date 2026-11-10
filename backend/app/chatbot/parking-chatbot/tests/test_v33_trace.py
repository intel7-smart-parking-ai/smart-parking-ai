"""로컬 턴 기록으로 Langfuse 트레이스처럼 추적(11/10): 호출별 시간·버려진 초안 기록, 리포트(tools/trace_report.py)."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import agents, config, engine as e, monitoring

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None
TOOLS = Path(__file__).resolve().parents[1] / "tools"


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class TraceRecordTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "turns.jsonl"

    def ask(self, text, *answers, sid="trace"):
        from test_agent_runtime_v3 import ScriptedModel
        from langchain_core.messages import AIMessage
        model = ScriptedModel([AIMessage(content=a) if isinstance(a, str) else a for a in answers])
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(config, "MONITOR_BACKEND", "local"), patch.object(config, "MONITOR_LOCAL_PATH", str(self.log)), \
                patch.object(agents, "run_conversation", side_effect=lambda *args: run(*args, model=model)):
            response = e.handle(sid, text=text)
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return response, rows[-1]

    def test_calls_are_recorded(self):
        _, row = self.ask("공항 자리 있어?", "김해공항 주차장에는 지금 빈자리가 있어요.")
        calls = row["agent"]["calls"]
        self.assertEqual([c["kind"] for c in calls], ["answer"])     # 미리 조회 뒤 답 쓰기 1회
        self.assertEqual(calls[0]["status"], "ok")
        self.assertIsInstance(calls[0]["ms"], int)

    def test_rejected_drafts_are_recorded(self):
        bad = "김해공항 주차장에는 지금 999자리가 비어 있어요."
        response, row = self.ask("공항 자리 있어?", bad, bad, sid="bad")
        self.assertNotIn("999", response["text"])
        drafts = row["agent"]["drafts"]
        self.assertEqual(drafts[0]["reason"], "unsupported_number")
        self.assertIn("999", drafts[0]["text"])
        self.assertTrue(drafts[-1].get("repair"))
        self.assertIn("repair_failed:unsupported_number", row["review"])

    def test_drafts_follow_capture_setting(self):
        bad = "김해공항 주차장에는 지금 999자리가 비어 있어요."
        with patch.object(config, "MONITOR_CAPTURE_TEXT", False):
            _, row = self.ask("공항 자리 있어?", bad, bad, sid="nocap")
        self.assertEqual(row["agent"]["drafts"][0]["text"], {"chars": len(bad)})


class TraceReportTests(unittest.TestCase):
    def load_tool(self):
        sys.path.insert(0, str(TOOLS))
        self.addCleanup(sys.path.remove, str(TOOLS))
        import trace_report
        return trace_report

    def test_report_and_sheet(self):
        trace_report = self.load_tool()
        rows = [
            {"at": "2026-11-10T16:00:00+0900", "session": "s1", "input": {"text": None, "event": "start"}, "output": {"route": "start:welcome"}},
            {"at": "2026-11-10T16:00:01+0900", "session": "s1", "input": {"text": "내차 1724 위치"}, "latency_ms": 10,
             "output": {"route": "guard:pass > rule:find_my_car", "text": "17가1724 차량은…"}, "review": []},
            {"at": "2026-11-10T16:00:05+0900", "session": "s1", "input": {"text": "스크래치 났어"}, "latency_ms": 3270,
             "output": {"route": "guard:vehicle_damage > agent:ungrounded > agent:dropped", "text": "차량 손상이나…"},
             "agent": {"status": "ungrounded", "model_calls": 2, "input_tokens": 5000, "output_tokens": 90,
                       "calls": [{"kind": "answer", "ms": 1501, "limit_s": 5.0, "status": "ok"}],
                       "drafts": [{"reason": "unsupported_number", "text": "… 17가1724 …"}]},
             "prompts": {"conversation": "1f658725"}, "review": ["repair_failed:unsupported_number"]},
        ]
        report, sheet = trace_report.build(rows, price_in=0.1, price_out=0.4)
        self.assertIn("질문 턴 2개", report)
        self.assertIn("이벤트 1개 제외", report)
        self.assertIn("repair_failed:unsupported_number", report)
        self.assertIn("버려진 초안(unsupported_number)", report)
        self.assertIn("앞 대화: 내차 1724 위치", report)
        self.assertEqual(sheet[0]["turns"], ["내차 1724 위치", "스크래치 났어"])   # 앞 대화까지 재현

    def test_empty_log(self):
        report, sheet = self.load_tool().build([])
        self.assertIn("기록이 없어요", report)
        self.assertEqual(sheet, [])


class FakeButtonAndDamageTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_fake_buttons_removed(self):
        from chatbot.conversation_turn import _strip_fake_buttons
        self.assertEqual(_strip_fake_buttons("요금은 3,000원이에요. [결제하기] [관리자 호출]"), "요금은 3,000원이에요.")
        self.assertEqual(_strip_fake_buttons("전달돼요.\n\n[관리자 호출]"), "전달돼요.")
        self.assertEqual(_strip_fake_buttons("배치도 [P1] 구역이 국내선이에요."), "배치도 [P1] 구역이 국내선이에요.")

    def test_damage_notice_names_looked_up_car(self):
        with patch.object(config, "LLM_PROVIDER", "none"):
            e.handle("dmg", text="내차 1724 위치")
            r = e.handle("dmg", text="스크래치 났어")
        self.assertIn("조회한 차량(17가1724 차량) 정보", r["text"])


if __name__ == "__main__":
    unittest.main()
