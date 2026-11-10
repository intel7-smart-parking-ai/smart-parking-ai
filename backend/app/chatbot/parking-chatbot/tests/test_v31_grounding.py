"""v3.1 근거 검사: 코드가 계산한 값은 통과, 거짓 실행·엉뚱한 숫자는 차단. LangChain 없이 실행된다."""
import asyncio
import threading
import unittest
from unittest.mock import patch

from chatbot import config
from chatbot.agents import conversation
from chatbot.agents.evidence import check_answer, contradicts_restricted_policy, no_tool_reply_ok
from chatbot.adapters import CachedAdapter, MockAdapter
from chatbot.tools import ReadTools
from chatbot.tools.read import clock_text, duration_text

A, B = config.catalog.name("A"), config.catalog.name("B")
ROWS = [
    {"lot_id": "A", "name": A, "zone": "ev", "empty": 3, "total": 4, "availability": "available", "reason": None,
     "base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000},
    {"lot_id": "B", "name": B, "zone": "ev", "empty": 5, "total": 6, "availability": "available", "reason": None,
     "base_minutes": 30, "base_fee": 1500, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}]
SUMMARY = {"lot_count": 2, "lots_with_space": 2, "empty_diff": 2, "base_fee_diff": 500,
           "notes": [f"{B}이 {A}보다 빈자리 2칸 많음", f"{A} 기본요금이 500원 저렴함"]}
ENTRY = "2026-11-09T14:05:00+09:00"
VEHICLE = {"car_id": "23", "errors": {}, "partial": False,
           "location": {"car_id": "23", "lot_id": "B", "space_id": "B-06", "entry_at": ENTRY,
                        "entry_time_text": clock_text(ENTRY), "elapsed_text": duration_text(75)},
           "fee": {"car_id": "23", "lot_id": "B", "fee": 2500, "minutes": 75, "paid": False}}
ESTIMATE = {"car_id": "23", "lot_id": "B", "current_fee": 2500, "after_minutes": 30, "estimated_fee": 4000,
            "increase": 1500, "estimate": True}
OBS = [{"tool": "compare_lots", "result": {"ok": True, "data": {"rows": ROWS, "summary": SUMMARY}}},
       {"tool": "vehicle_info", "result": {"ok": True, "data": VEHICLE}},
       {"tool": "estimate_fee", "result": {"ok": True, "data": ESTIMATE}}]
QUESTION = "23번 차 얼마나 있었고 30분 뒤 나가면 얼마야? 충전자리 비교도"


class GroundingTests(unittest.TestCase):
    def test_combined_natural_answers_pass(self):
        for answer in [f"{B}이 2자리 더 많아요.", "2곳 모두 자리가 있어요.", "23번 차는 오후 2시 5분에 들어왔어요.",
                       "23번 차는 1시간 15분째 주차 중이에요.", "30분 뒤 나가면 약 4,000원이에요.",
                       f"{A} 기본요금이 500원 더 싸요.", "지금 2,500원이고 30분 뒤면 1,500원 더 늘어요.",
                       f"{A}은 처음 30분 1,000원이에요.", "결제는 아직 안 됐어요. 원하시면 결제 버튼을 눌러 주세요.",
                       "관리자를 부르려면 아래 버튼을 눌러 주세요."]:
            with self.subTest(answer=answer):
                self.assertIsNone(check_answer(answer, QUESTION, OBS))

    def test_false_actions_promises_and_misattributed_numbers_are_blocked(self):
        for answer in [f"{A}이 1,000원 더 싸요.", f"{B}은 30분에 500원이라 더 싸요.", f"{B}이 3자리 더 많아요.",
                       "결제해 드렸어요.", "결제 완료했어요.", "관리자를 불렀어요.", "관리자 호출해 드렸습니다.",
                       "관리자에게 연락했어요.", "직원이 곧 갈 거예요.", "차단기를 열어 드렸어요.",
                       f"목적지를 {B}으로 바꿨어요.", "결제가 끝났어요.", "정산 처리됐습니다.", "23번 차는 C-06에 있어요."]:
            with self.subTest(answer=answer):
                self.assertIsNotNone(check_answer(answer, QUESTION, OBS))

    def test_existing_paid_record_or_registered_call_can_be_described(self):
        paid = [{"tool": "vehicle_info", "result": {"ok": True, "data": {**VEHICLE, "fee": {**VEHICLE["fee"], "paid": True}}}}]
        self.assertIsNone(check_answer("23번 차는 이미 결제됐어요.", "23번 요금", paid))
        self.assertIsNotNone(check_answer("23번 차는 제가 결제해 드렸어요.", "23번 요금", paid))
        called = [{"tool": "call_status", "result": {"ok": True, "data": {"has_call": True, "call_no": 3}}}]
        self.assertIsNone(check_answer("관리자 문의가 접수돼 있어요(문의 3번).", "문의 됐어?", called))

    def test_small_talk_and_policy_guard(self):
        self.assertTrue(no_tool_reply_ok("고마워", "천만에요! 또 궁금한 게 있으면 물어보세요."))
        self.assertFalse(no_tool_reply_ok("자리 있어?", "네, 자리 넉넉해요."))
        self.assertFalse(no_tool_reply_ok("안녕", "안녕하세요! 지금 공항은 한산해요."))
        self.assertTrue(contradicts_restricted_policy("일반차도 주차할 수 있어요."))
        self.assertFalse(contradicts_restricted_policy("일반 차량은 전기차 전용 칸에 주차할 수 없어요."))


class ComputedToolTests(unittest.TestCase):
    def setUp(self):
        self.tools = ReadTools(CachedAdapter(MockAdapter()), lambda: {"level": 0}, allowed_car_id="23")

    def test_texts(self):
        self.assertEqual(clock_text("2026-11-09T14:05:00+09:00"), "오후 2시 5분")
        self.assertEqual(clock_text("2026-11-09T00:00:00+09:00"), "오전 12시")
        self.assertEqual(duration_text(75), "1시간 15분")
        self.assertEqual(duration_text(1500), "1일 1시간")

    def test_compare_and_estimates_are_code_computed(self):
        compared = self.tools.compare_lots(["C", "D"], "ev")["data"]["summary"]
        self.assertEqual(compared["lot_count"], 2)
        self.assertIn("base_fee_diff", compared)
        estimate = self.tools.estimate_fee(30, car_id="23")
        self.assertTrue(estimate["ok"], estimate)
        self.assertTrue(estimate["data"]["estimate"])
        self.assertEqual(estimate["data"]["increase"], estimate["data"]["estimated_fee"] - estimate["data"]["current_fee"])
        self.assertFalse(self.tools.estimate_fee(30, car_id="24")["ok"])  # 허용된 차량만
        self.assertFalse(self.tools.estimate_fee(0, lot_id="C")["ok"])

    def test_estimate_refuses_when_server_fee_rule_differs(self):
        with patch.object(self.tools.adapter, "get_fee", return_value={
                "car_id": "23", "lot_id": "A", "fee": 777, "minutes": 40, "paid": False, "entry_at": "2026-11-09T14:05:00+09:00"}), \
                patch.object(self.tools, "_vehicle", return_value={"car_id": "23", "lot_id": "A", "space_id": "A-03",
                                                                    "entry_at": "2026-11-09T14:05:00+09:00"}):
            self.assertFalse(self.tools.estimate_fee(30, car_id="23")["ok"])

    def test_read_only_admin_status(self):
        tools = ReadTools(CachedAdapter(MockAdapter()), lambda: {}, admin_status_provider=lambda: {"id": None})
        self.assertEqual(tools.call_status()["data"], {"has_call": False})


class SlotTests(unittest.TestCase):
    def test_waits_briefly_for_a_free_model_slot(self):
        with patch.object(config, "LLM_MAX_INFLIGHT", 1):
            slots = conversation._slots()
            self.assertTrue(slots.acquire(blocking=False))
            threading.Timer(0.1, slots.release).start()
            self.assertTrue(asyncio.run(conversation._acquire_slot(slots, 1.0)))
            slots.release()
            self.assertTrue(slots.acquire(blocking=False))
            self.assertFalse(asyncio.run(conversation._acquire_slot(slots, 0.1)))
            slots.release()


if __name__ == "__main__":
    unittest.main()
