"""v3.3: 김채린 turn_policy + v3.2-safety-port turn_guard를 고쳐 옮긴 것의 회귀 검사.

후보(feature/KAN-9-chatbot-v32-safety-port, 74fd09b) 검토에서 바꾼 점
- Lv2 중에는 끼어들지 않음(경보 위치·출구 안내 유지), 연기 신고에 현재 모의 경보 상태를 덧붙임
- 허용 여부는 팀 결정 D-05 문장만 사실로 사용(근거 없는 법 조건·일반 칸 허용 단정 제거)
- 진행 중인 결제 확인을 지우지 않음, 별칭 주차장의 없는 칸, '주차장 이름'은 목록 안내
"""
import unittest
import uuid

from chatbot import config, engine as e
from chatbot.turn_guard import guard


class _NoPending:
    pending = None


D05 = "일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다."


class TurnGuardTests(unittest.TestCase):
    ANSWERS = {
        "연기 나는 것 같아": "119",
        "거기 연기 나요": "대피",
        "타는 냄새 나": "대피",
        "잠깐 5분만 장애인 칸에 대도 돼?": D05,
        "일반차인데 전기차 칸 비었으면 대도 돼?": D05,
        "일반차도 전기차 전용 칸에 주차할 수 있어?": D05,
        "전기차인데 일반 칸에 대도 돼?": "안내 자료에 없어요",
        "그거 취소해줘": "어떤 요청을 취소할까요",
        "부산역 B-30 비었어?": "B-30 칸이 없어요",
        "김해공항 C-05 비었어?": "C-05 칸이 없어요",
        "공항 C-05 비었어?": "C-05 칸이 없어요",
        "백화점 D-17 비었어?": "D-17 칸이 없어요",
        "넌 이름이 뭐니": "PARK:ON 주차 안내 챗봇",
        "너 누구야?": "PARK:ON 주차 안내 챗봇",
        "안녕하세요": "PARK:ON 주차 안내 챗봇",
        "What is your name?": "PARK:ON 주차 안내 챗봇",
        "주차장 이름이 뭐야?": "4곳",
    }
    PASS_THROUGH = (
        "장애인 주차 가능한 곳 어디야?", "전기차 자리 가능한 곳 있어?", "장애인 자리 이용 가능한 주차장?",
        "주차 연기 나중에 할게", "예약 연기 나중에", "연기 학원 근처 주차장", "B-06 비었어?", "부산역 A-03 비었어?",
        "김해공항 C-04 비었어?", "신세계 D-16 자리 있어?", "결제 취소해줘", "24번 차 어디야",
        "관리자 부르면 돈 들어?", "안녕 23번 차 어디야", "내 차 이름 뭐야", "구역 이름이 뭐야",
    )

    def test_guarded_answers(self):
        for text, part in self.ANSWERS.items():
            with self.subTest(text=text):
                reply, _ = guard(_NoPending(), text)
                self.assertIsNotNone(reply)
                self.assertIn(part, reply)

    def test_normal_questions_pass_through(self):
        for text in self.PASS_THROUGH:
            with self.subTest(text=text):
                reply, rewritten = guard(_NoPending(), text)
                self.assertIsNone(reply)
                self.assertEqual(rewritten, text)

    def test_no_unapproved_policy_facts(self):
        for text in ("장애인 칸 비었는데 대도 돼?", "전기차인데 일반 칸 대도 돼?", "장애인 차량인데 일반 칸 대도 돼?"):
            with self.subTest(text=text):
                reply, _ = guard(_NoPending(), text)
                self.assertNotIn("보행상", reply)
                self.assertNotIn("주차할 수 있어요", reply)

    def test_lv2_defers_to_evacuation(self):
        for text in ("연기 나요", "안녕", "넌 누구야", "부산역 B-30 비었어?"):
            with self.subTest(text=text):
                reply, rewritten = guard(_NoPending(), text, {"level": 2, "lot_id": "A"})
                self.assertIsNone(reply)
                self.assertEqual(rewritten, text)


class TurnGuardEngineTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_smoke_report_includes_mock_alert_state(self):
        sid = uuid.uuid4().hex
        e.handle(sid, event="start")
        r = e.handle(sid, text="연기 나는 것 같아")
        self.assertEqual(r["meta"]["intent"], "emergency")
        self.assertIn("119", r["text"])
        self.assertIn("모의 화재 경보는 없어요", r["text"])

    def test_lv2_keeps_alert_specific_evacuation(self):
        e.data.set_alert(2, "A", 3)
        sid = uuid.uuid4().hex
        e.handle(sid, event="start")
        r = e.handle(sid, text="연기 나요")
        self.assertIn("출구", r["text"])
        self.assertNotEqual(r["meta"].get("intent"), "emergency")

    def test_side_question_keeps_payment_confirmation(self):
        sid = uuid.uuid4().hex
        e.handle(sid, event="start")
        self.assertEqual(e.handle(sid, text="44번 차 결제해줘")["flow"], "confirm_vehicle")
        r = e.handle(sid, text="장애인 칸 대도 돼?")
        self.assertIn(D05, r["text"])
        self.assertEqual(r["flow"], "confirm_vehicle")
        self.assertNotIn("확인할 내용이 없어요", e.handle(sid, text="네")["text"])

    def test_smoke_report_ends_pending_confirmation(self):
        sid = uuid.uuid4().hex
        e.handle(sid, event="start")
        e.handle(sid, text="44번 차 결제해줘")
        r = e.handle(sid, text="주차장에 연기 나요")
        self.assertEqual(r["flow"], "idle")


if __name__ == "__main__":
    unittest.main()
