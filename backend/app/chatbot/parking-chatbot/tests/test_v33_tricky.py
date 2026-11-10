"""v3.3: 시연에서 나올 만한 꼬는 질문(tests/dialogs/tricky_demo.json) 중 모델 없이도 지켜야 하는 것.

모델이 꺼졌거나 시간 초과여도 규칙 답이 문장과 맞아야 한다.
- '나 전기차야' 같은 자기 차 종류를 기억하고 그 칸 기준으로 안내
- '전기차' 안의 '기차'를 기차 목적으로 읽지 않음
- 한 주차장으로 좁힌 뒤 '거기'는 그 주차장
- '스물세번 차'(고유어 숫자), '내 차 번호 까먹었어'
- 범위 밖 정중한 거절(주차장 성격만 언급)은 근거 검사 통과
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import config, engine as e, plan
from chatbot.agents.evidence import no_tool_reply_ok
from chatbot.info_gaps import hinted_lot
from chatbot.parser import extract_vehicle


class TrickyRuleTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def talk(self, *turns, sid="tricky"):
        with patch.object(config, "LLM_PROVIDER", "none"):
            return [e.handle(sid, text=t) for t in turns]

    def test_self_vehicle_is_remembered(self):
        first, where, airport = self.talk("나 전기차야", "어디 대면 돼?", "공항 자리 있어?")
        self.assertIn("전기차시군요", first["text"])
        self.assertIn("충전 구역", first["text"])
        self.assertIn("충전 구역", where["text"])
        self.assertIn("김해공항 주차장 충전 구역", airport["text"])
        self.assertEqual(e.get_session("tricky").vehicle_type, "ev")

    def test_accessible_and_general_statements(self):
        r = self.talk("장애인 차량이에요", "공항에 자리 있나요?", sid="acc")
        self.assertIn("김해공항 주차장 장애인 구역", r[1]["text"])
        r = self.talk("제 차는 일반차예요", sid="gen")[0]
        self.assertIn("일반 차량은 전기차·장애인 전용 칸에 주차할 수 없어요", r["text"])

    def test_self_vehicle_detection(self):
        cases = {"나 전기차야": ("ev", True), "저 장애인 차량이에요": ("accessible", True),
                 "나 전기차인데 공항 자리 있어?": ("ev", False), "전기차 칸 어디야": (None, False),
                 "일반차도 전기차 칸 대도 돼?": (None, False)}
        for text, expected in cases.items():
            with self.subTest(text=text):
                p = plan.make(text)
                self.assertEqual((p.self_vehicle, p.self_only), expected)

    def test_ev_is_not_train(self):
        self.assertIsNone(hinted_lot("나 전기차야"))
        self.assertEqual(hinted_lot("기차 타러 가"), "B")

    def test_there_follows_narrowed_lot(self):
        r = self.talk("나 전기차야", "공항 자리 있어?", "거기 장애인 칸은?", sid="there")[-1]
        self.assertIn("김해공항 주차장", r["text"])
        self.assertNotIn("부산역주차장", r["text"])
        r = self.talk("공항 자리 있어?", "거기 충전 칸은?", sid="there2")[-1]
        self.assertIn("김해공항 주차장", r["text"])

    def test_native_korean_car_numbers(self):
        self.assertEqual(extract_vehicle("스물세번 차 어디")[0], "23")
        self.assertEqual(extract_vehicle("서른두 번 차량 요금")[0], "32")
        for text in ("열 번 물어봤어", "다시 한번 차 찾아줘", "세번째 칸"):
            with self.subTest(text=text):
                self.assertIsNone(extract_vehicle(text)[0])
        self.assertIn("23번 차", self.talk("스물세번 차 어디", sid="native")[0]["text"])

    def test_forgot_number(self):
        r = self.talk("내 차 번호 까먹었어", sid="forgot")[0]
        self.assertIn("스티커", r["text"])
        self.assertIn("관리자", r["text"])
        self.assertEqual(r["meta"]["route"], "guard:forgot_number")

    def test_polite_out_of_scope_with_lot_profile_passes(self):
        answer = ("죄송하지만 맛집 정보는 제가 알려 드릴 수 없어요. 쇼핑이면 신세계백화점주차장, "
                  "기차 이용이면 부산역주차장이 맞는지 확인해 볼 수 있어요.")
        self.assertTrue(no_tool_reply_ok("근처 맛집 알려줘", answer))
        self.assertFalse(no_tool_reply_ok("근처 맛집 알려줘", "신세계백화점주차장이 한산해요."))
        self.assertFalse(no_tool_reply_ok("어디가 좋아?", "부산역주차장이 좋아요."))


class FallbackWithoutModelTests(unittest.TestCase):
    """모델 키 한도 초과·시간 초과 때 나가는 규칙 답(11/09 OpenRouter 한도 소진으로 실제 발생)."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def talk(self, sid, *turns):
        with patch.object(config, "LLM_PROVIDER", "none"):
            return [e.handle(sid, text=t)["text"] for t in turns]

    def test_own_car_statement(self):
        where, fee = self.talk("own", "23번이 내 차야", "얼마 나왔어?")
        self.assertIn("23번 차", where)
        self.assertIn("원이에요", fee)
        self.assertNotIn("차량 번호를 알려주세요", fee)

    def test_several_purposes_or_lots_are_compared(self):
        trip = self.talk("trip", "여행 계획 중인데 먼저 기차 타고 나중에 비행기로 이동할 건데 어느 주차장에 가야 할까")[0]
        self.assertIn("부산역주차장", trip)
        self.assertIn("김해공항 주차장", trip)
        each = self.talk("each", "친구는 공항 가고 나는 백화점 갈 건데 각각 자리 있어?")[0]
        self.assertIn("김해공항 주차장", each)
        self.assertIn("신세계백화점주차장", each)

    def test_fee_without_payment(self):
        r = self.talk("nopay", "결제는 하지 말고 23번 요금만 알려줘")[0]
        self.assertIn("원이에요", r)
        self.assertNotIn("진행하지 않을게요", r)

    def test_lot_fee_follow_ups(self):
        _, fee, three = self.talk("follow", "공항 자리 있어?", "요금은?", "3시간이면?")
        self.assertIn("김해공항 주차장", fee)
        self.assertIn("처음 30분", fee)
        self.assertIn("예상 요금은 15,000원", three)
        self.assertIn("처음 30분", self.talk("lotfee", "김해공항 요금은?")[0])

    def test_lot_filled_from_plan(self):
        self.assertIn("신세계백화점주차장", self.talk("full", "백화점 꽉 찼어?")[0])

    def test_out_of_scope_light_and_gibberish(self):
        self.assertIn("범위가 아니에요", self.talk("food", "근처 맛집 알려줘")[0])
        self.assertIn("이해하지 못했어요", self.talk("gib", "ㅁㄴㅇㄹ")[0])
        self.assertIn("경보", self.talk("light", "불 꺼져 있던데 정상이야?")[0])
        self.assertEqual(plan.minutes("1시간 30분 세우면"), 90)
        self.assertIsNone(plan.minutes("30분 뒤 도착"))


class NicknameDurationStructureTests(unittest.TestCase):
    """11/09 밤: 별명('역' 등), 기간 요금('3일'), '넵', 구조 점검에서 고친 것."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def talk(self, sid, *turns):
        with patch.object(config, "LLM_PROVIDER", "none"):
            return [e.handle(sid, text=t) for t in turns]

    def test_day_durations(self):
        self.assertEqual(plan.minutes("김해공항 3일 주차할건데 예상 요금"), 4320)
        self.assertEqual(plan.minutes("이틀 동안 세우면"), 2880)
        self.assertIsNone(plan.minutes("3일에 가면 자리 있어?"))
        self.assertEqual(plan.minutes("5일 주차하면 얼마"), -1)
        r = self.talk("days", "김해공항 3일 주차할건데 예상 요금")[0]["text"]
        self.assertIn("3일 세우면 예상 요금은 120,000원", r)
        self.assertNotIn("차량 번호", r)
        self.assertIn("3일까지만", self.talk("long", "김해공항 5일 주차하면 얼마")[0]["text"])

    def test_ambiguous_nicknames_get_guess_note(self):
        cases = {"역 주차장 자리 있어?": "부산역주차장을 말씀하시는 거라면", "김해 자리 있어?": "김해공항 주차장을 말씀하시는 거라면",
                 "센텀 주차 돼?": "신세계백화점주차장을 말씀하시는 거라면"}
        for text, note in cases.items():
            with self.subTest(text=text):
                self.assertTrue(self.talk("nick-" + text[:2], text)[0]["text"].startswith(note))
        for text in ("공항 자리 있어?", "부산역 자리 있어?", "서면역 주차장 자리"):
            with self.subTest(text=text):
                self.assertNotIn("말씀하시는 거라면", self.talk("plain-" + text[:2], text)[0]["text"])

    def test_nep_confirms(self):
        quote = self.talk("nep", "44번 차 결제해줘", "넵")[1]
        self.assertEqual(quote["flow"], "confirm_action")

    def test_asked_lot_names_pass_evidence(self):
        from chatbot.agents.evidence import check_answer
        obs = [{"tool": "parking_status", "result": {"ok": True, "data": {"rows": [
            {"lot_id": "D", "name": "신세계백화점주차장", "zone": None, "empty": 62}]}}}]
        self.assertIsNone(check_answer("김해공항을 빼면 신세계백화점주차장에 62자리가 있어요.", "공항 말고 자리 많은 곳", obs))
        self.assertEqual(check_answer("부산역주차장도 있어요.", "공항 말고 자리 많은 곳", obs), "unsupported_lot")
        self.assertEqual(check_answer("김해공항은 40자리예요.", "공항 말고 자리 많은 곳", obs), "unsupported_number")

    def test_ev_word_is_not_station_in_evidence_check(self):
        # 11/09 실제 모델: '김해공항 주차장의 전기차 충전 칸은…'이 '기차'(부산역 별칭) 때문에 버려지던 문제
        from chatbot.agents.evidence import _mentions, check_answer
        self.assertEqual({lot for _, lot in _mentions("김해공항 주차장의 전기차 충전 칸은 6칸이에요.")}, {"C"})
        self.assertEqual({lot for _, lot in _mentions("기차 타시면 부산역주차장이에요")}, {"B"})
        obs = [{"tool": "parking_status", "result": {"ok": True, "data": {"rows": [
            {"lot_id": "C", "name": "김해공항 주차장", "zone": "ev", "empty": 6}]}}}]
        self.assertIsNone(check_answer("김해공항 주차장의 전기차 충전 칸은 6칸이 비어 있어요.", "거기 충전 칸은?", obs))

    def test_repair_needs_enough_time(self):
        from chatbot.agents import conversation
        # 고쳐 쓰기는 답을 살리는 경로라 너무 높이지 않는다(1~2초)
        self.assertTrue(1.0 <= conversation._repair_min_sec() <= 2.0)


class TrickySetFileTests(unittest.TestCase):
    def test_question_set_is_well_formed(self):
        spec = json.loads((Path(__file__).parent / "dialogs" / "tricky_demo.json").read_text(encoding="utf-8"))
        ids = [d["id"] for d in spec["dialogs"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 30)
        for d in spec["dialogs"]:
            self.assertTrue(d["turns"] and d["expect"] and d["kind"])


if __name__ == "__main__":
    unittest.main()
