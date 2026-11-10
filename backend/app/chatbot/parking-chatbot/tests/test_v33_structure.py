"""v3.3 구조화: 경로 기록(route), 계획표(plan), 역할별 지시문(prompts) 회귀 검사.

답 내용은 바꾸지 않고, 어떤 단계를 거쳤는지 기록되고 같은 계획표를 함께 보는지 확인한다.
"""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import config, engine as e, monitoring, plan, prompts, route


class RouteTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def ask(self, text, sid=None):
        with patch.object(config, "LLM_PROVIDER", "none"):
            sid = sid or "route-" + text[:5]
            return e.handle(sid, text=text)

    def test_every_response_has_route(self):
        self.assertEqual(e.handle("r0", event="start")["meta"]["route"], "start:welcome")
        self.assertEqual(e.handle("r0", chip_id="empty_all")["meta"]["route"], "chip:empty_all")

    def test_stage_order_and_outcomes(self):
        cases = {
            "연기 나요": "guard:emergency",
            "롯데백화점 주차장 빈자리": "guard:pass > plan:vacancy > boundary:unknown_lot",
            "44번 차 결제해줘": "guard:pass > plan:payment > boundary:pass > workflow:pay_fee > rule:pay_fee",
            "지금 빈자리 있어?": "guard:pass > plan:vacancy > boundary:pass > agent:off > fallback:get_empty_spots",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.ask(text)["meta"]["route"], expected)

    def test_fallback_records_changed_reading(self):
        # '오늘 주차장 분위기 어때?'의 첫 규칙 해석은 '이해 못 함' → 계획표가 빈자리로 바꾸고 이유를 남긴다
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "turns.jsonl"
            with patch.object(config, "MONITOR_BACKEND", "local"), patch.object(config, "MONITOR_LOCAL_PATH", str(path)), \
                    patch.object(config, "LLM_PROVIDER", "none"):
                monitoring.reset_for_tests()
                r = e.handle("why", text="오늘 주차장 분위기 어때?")
            steps = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["route"]
        monitoring.reset_for_tests()
        last = steps[-1]
        self.assertEqual((last["stage"], last["outcome"]), ("fallback", "get_empty_spots"))
        self.assertIn("첫 해석 unknown", last["why"])
        self.assertTrue(r["meta"]["route"].endswith("fallback:get_empty_spots"))

    def test_lv2_route_skips_guard_and_agent(self):
        e.data.set_alert(2, "A", 3)
        r = self.ask("연기 나요", "lv2")
        self.assertIn("guard:pass", r["meta"]["route"])
        self.assertIn("agent:skip", r["meta"]["route"])

    def test_summary_format(self):
        self.assertEqual(route.summary([{"stage": "guard", "outcome": "pass"}, {"stage": "agent", "outcome": "ok"}]),
                         "guard:pass > agent:ok")
        self.assertEqual(route.summary([]), "")


class PlanTests(unittest.TestCase):
    def test_readings(self):
        p = plan.make("현재 우리가 일본 여행중인데 친구가 올거거든 주차장 자리가 얼마나 여유가 있어?")
        self.assertEqual((p.purpose_lot, p.wants), ("C", ("vacancy",)))
        self.assertTrue(p.talk["spot"] and not p.talk["money"])
        p = plan.make("23번 차 요금이랑 위치")
        self.assertEqual((p.car, set(p.wants)), ("23", {"fee", "location"}))
        p = plan.make("공항이랑 신세계 중 어디가 싸?")
        self.assertEqual(p.lots, ("C", "D"))
        self.assertIn("compare", p.wants)
        self.assertEqual(plan.make("부산역 장애인 칸").zone, "accessible")
        self.assertEqual(plan.make("").wants, ())

    def test_plate_is_car_talk_and_bare_station_is_busan(self):
        self.assertEqual(plan.make("17가1724 어디 주차했어?").wants, ("location",))
        bare = plan.make("역에 주차장 장애인 자리 몇개나 남았어?")
        self.assertEqual((bare.lots, bare.guessed), (("B",), {"B": "역"}))   # 단독 '역'은 부산역의 별명
        for text in ("서면역 주차장", "해운대역 근처", "역사 공부"):
            with self.subTest(text=text):
                self.assertEqual((plan.make(text).lots, plan.make(text).guessed), ((), {}))
        e.reset_all()
        with patch.object(config, "LLM_PROVIDER", "none"):
            r = e.handle("station", text="역에 주차장 장애인 자리 몇개나 남았어?")
        self.assertIn("부산역주차장", r["text"].split("\n")[0])

    def test_one_plan_shared_by_agent_hints_and_fallback(self):
        from chatbot.conversation_turn import readings
        p = plan.make("쇼핑하고 싶은데 자리 남았나")
        token = plan.use(p)
        try:
            self.assertIs(plan.for_text(p.text), p)
            self.assertEqual(readings(p.text), p.hints())
            self.assertIsNot(plan.for_text("다른 문장"), p)
        finally:
            plan.release(token)
        self.assertEqual(p.hints()["purpose_lot"], "D")


class PromptTests(unittest.TestCase):
    def test_one_file_per_role(self):
        for role, name in prompts.ROLES.items():
            with self.subTest(role=role):
                self.assertTrue((Path(prompts.__file__).parent / name).is_file())
                self.assertRegex(prompts.version(role), r"^[0-9a-f]{8}$")
        self.assertEqual(set(prompts.versions()), set(prompts.ROLES))

    def test_rendered_prompts_have_no_leftover_slots(self):
        from chatbot import llm, response_locale as rl
        rendered = {
            "translate_answer": prompts.render("translate_answer", language="English"),
            "translate_question": prompts.render("translate_question", language="English", lot_hints=rl._LOT_HINTS),
            "intent_classifier": llm.system_prompt(),
        }
        for role, text in rendered.items():
            with self.subTest(role=role):
                self.assertNotRegex(text, r"\{[a-z_]+\}")
        self.assertIn('{"korean": "..."}', rendered["translate_question"])
        self.assertIn('"intent":"get_empty_spots"', rendered["intent_classifier"])
        self.assertEqual(prompts.conversation_prompt(), prompts.load("conversation"))


class MonitoringRecordTests(unittest.TestCase):
    def setUp(self):
        monitoring.reset_for_tests()
        e.reset_all()
        self.addCleanup(monitoring.reset_for_tests)
        self.addCleanup(e.reset_all)

    def test_local_record_has_route_and_prompt_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "turns.jsonl"
            with patch.object(config, "MONITOR_BACKEND", "local"), patch.object(config, "MONITOR_LOCAL_PATH", str(path)), \
                    patch.object(config, "LLM_PROVIDER", "none"):
                e.handle("mon", text="44번 차 결제해줘", request_id="rq-1")
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual([s["stage"] for s in row["route"]][-2:], ["workflow", "rule"])
        self.assertEqual(row["output"]["route"].split(" > ")[-1], "rule:pay_fee")
        self.assertEqual(set(row["prompts"]), set(prompts.ROLES))


AVAILABLE = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx"))


@unittest.skipUnless(AVAILABLE, "fastapi/httpx 미설치")
class TranslationRouteTests(unittest.TestCase):
    def test_foreign_question_route_has_translation_steps(self):
        from fastapi.testclient import TestClient
        from server_fastapi import app
        e.reset_all()
        with TestClient(app) as client, patch.object(config, "LLM_PROVIDER", "none"):
            r = client.post("/api/v1/chat", json={"session_id": "tr", "text": "Any empty spots?", "language": "en"}).json()
        self.assertRegex(r["meta"]["route"], r"^translate_in:kept_original > .* > translate_out:en$")


if __name__ == "__main__":
    unittest.main()
