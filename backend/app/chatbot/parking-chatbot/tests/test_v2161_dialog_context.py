"""v2.16.1: 대화 맥락(지시어·생략·선택)과 데이터 집계형 답변. LLM 호출 없음."""
from copy import deepcopy
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, insights, parser
from chatbot.adapters import CachedAdapter, MockAdapter
from safety_check import assert_safe

DIALOGS = Path(__file__).parent / "dialogs"


class Base(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter())); p.start(); self.addCleanup(p.stop); e.reset_all()
        self.addCleanup(config.catalog.configure, deepcopy(config.catalog.settings))

    def ask(self, text, sid="s"):
        return e.handle(sid, text=text)


class DialogEvalTests(unittest.TestCase):
    def test_dev_and_holdout_conversations_all_pass(self):
        import run_dialog_eval
        for name in ("natural_dev.json", "natural_holdout.json"):
            with self.subTest(set=name):
                passed, total = run_dialog_eval.run([DIALOGS / name])
                self.assertEqual(passed, total)


class ContextTests(Base):
    def test_anaphora_uses_last_lot_and_history_keeps_original_text(self):
        self.ask("김해공항 빈자리")
        r = self.ask("거기로 안내해줘")
        self.assertIn("김해공항 주차장을 목적지로", r["text"])
        self.assertIn(("user", "거기로 안내해줘"), list(e.get_session("s").history))

    def test_explicit_lot_beats_context(self):
        self.ask("김해공항 빈자리")
        r = self.ask("개발원 주차장 빈자리는?")
        self.assertEqual(r["card"]["data"]["focus"], "A")

    def test_context_expires(self):
        self.ask("김해공항 빈자리")
        s = e.get_session("s")
        s.focus_at = s.query_context_at = time.monotonic() - config.QUERY_CONTEXT_TTL_SEC - 1
        s.offered_at = 0
        r = self.ask("거기로 안내해줘")
        self.assertNotIn("목적지로", r["text"])

    def test_choice_only_after_options_were_offered(self):
        r = self.ask("공항")
        self.assertEqual(r["meta"]["intent"], "unknown")
        self.ask("빈자리 몇 개 있어?")
        self.assertEqual(self.ask("공항")["card"]["data"]["focus"], "C")

    def test_ordinal_is_not_a_vehicle_number(self):
        self.ask("빈자리 몇 개 있어?")
        r = self.ask("2번째")
        self.assertEqual(r["card"]["data"]["focus"], "B")
        self.assertIsNone(e.get_session("s").car_id)

    def test_tie_asks_instead_of_guessing(self):
        self.ask("어디가 제일 널널해?")
        r = self.ask("거기로 가줘")
        self.assertIn("골라", r["text"])
        self.assertIsNone(e.get_session("s").destination)

    def test_safety_still_wins_with_context(self):
        self.ask("신세계 빈자리")
        self.assertEqual(self.ask("거기 불났어?")["meta"]["intent"], "get_alerts")
        self.assertEqual(self.ask("거기서 대피 어디로 해?")["meta"]["intent"], "evacuation_guide")

    def test_vehicle_questions_unchanged(self):
        self.ask("김해공항 빈자리")
        self.assertEqual(self.ask("23번 차 어디 있어?")["meta"]["intent"], "find_my_car")


class InsightTests(Base):
    def test_ranking_excludes_alert_lot_and_says_so(self):
        e.data.set_alert(2, "C", 3); e.current_alert()
        r = self.ask("어디가 제일 널널해?")
        self.assertIn("신세계백화점주차장이 62자리로 가장 여유", r["text"])
        self.assertIn("김해공항 주차장 경보 중", r["text"])

    def test_occupancy_matches_snapshot(self):
        snap = e.data.get_empty_spots()["lots"]["A"]
        r = self.ask("개발원 주차장 얼마나 찼어?")
        self.assertIn(f"{snap['total']}칸 중 {snap['total'] - snap['empty'] - snap['uncertain']}칸", r["text"])

    def test_section_counts_come_from_space_states(self):
        states = e.data.get_empty_spots()["space_states"]["C"]
        empty = sum(v == "empty" for k, v in states.items() if k.startswith("B-"))
        r = self.ask("김해공항 B구역 빈자리 몇 개야?")
        self.assertIn(f"B 구역은 36칸 중 {empty}자리", r["text"])

    def test_bare_letter_section_without_context_keeps_lot_meaning(self):
        self.assertEqual(parser.parse_rules("B구역 빈자리").params, {"lot_id": "B"})

    def test_fee_rank_only_compares_policy_values(self):
        policies = {"A": {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000},
                    "B": {"base_minutes": 30, "base_fee": 2000, "unit_minutes": 10, "extra_fee": 700, "daily_cap": 20000}}
        text = insights.fee_rank(policies)
        self.assertIn("개발원주차장이 처음 30분 1,000원으로 가장 저렴", text)
        self.assertIn("서버가 계산", text)

    def test_not_parking_recommendations_stay_unknown(self):
        for text in ["주차장 근처 맛집 추천해줘", "점심 메뉴 추천해 줄래", "오늘 날씨 어때?"]:
            with self.subTest(text=text):
                self.assertEqual(parser.parse_rules(text).intent, "unknown")

    def test_new_routes_do_not_call_a_model(self):
        with patch("chatbot.llm.classify", side_effect=AssertionError("model")), \
             patch("chatbot.llm_router.route", side_effect=AssertionError("model")):
            for text in ["어디가 제일 널널해?", "개발원이랑 부산역 중 어디가 자리 많아?", "어디가 제일 싸?", "공항 주차장 몇 퍼센트 찼어?"]:
                with self.subTest(text=text):
                    self.assertNotEqual(parser.parse(text).intent, "unknown")


class SafetyOracleTests(Base):
    """test_robust_environment(FastAPI 50건)와 같은 assert_safe를 새 답변 전부에 상태별로 적용한다."""
    QUESTIONS = ["빈자리", "빈자리 몇 개 있어?", "충전 구역 빈자리", "어디가 제일 널널해?", "전기차 자리 제일 많은 곳",
                 "개발원이랑 부산역 중 어디가 자리 많아?", "공항이랑 신세계 중 어디가 더 여유 있어?",
                 "개발원 주차장 얼마나 찼어?", "전체 주차장 몇 퍼센트 찼어?", "김해공항 B구역 빈자리 몇 개야?",
                 "신세계 D구역 빈자리", "공항 주차장에서 제일 빈 구역은 어디야?", "어디가 제일 싸?",
                 "비행기 출발 3시간 전에 가면 공항 주차장 혼잡할까?", "김해공항 국제선 청사랑 가장 가까운 구역 어디야?"]

    def check_all(self, label):
        for i, text in enumerate(self.QUESTIONS):
            with self.subTest(state=label, text=text):
                assert_safe(e.handle(f"{label}-{i}", text=text))

    def test_normal(self):
        self.check_all("normal")

    def test_camera_uncertain_spaces(self):
        e.data.set_uncertain("C", 1, True); e.data.set_uncertain("A", 4, True)
        self.check_all("uncertain")

    def test_restriction_and_alert(self):
        e.data.set_restriction("D", True, "점검"); e.data.set_alert(1, "A", 2); e.current_alert()
        self.check_all("restricted")

    def test_lv2_alert(self):
        e.data.set_alert(2, "C", 3); e.current_alert()
        self.check_all("lv2")


if __name__ == "__main__":
    unittest.main()
