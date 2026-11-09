"""v3.2 이식: 안전·허용 여부·취소 확인·없는 칸·빨간 불 (김채린 turn_policy.py).

공통 질문지(11/09) 비교에서 v3.2 단독이 놓친 K13·K14·K15·K18·K19와,
일반 조회 문장이 잘못 걸리지 않는지를 함께 검사한다.
"""
import unittest
import uuid

from chatbot import engine as e
from chatbot.turn_guard import guard


class _NoPending:
    pending = None


class TurnGuardTests(unittest.TestCase):
    ANSWERS = {
        "연기 나는 것 같아": "119",
        "거기 연기 나요": "대피",
        "타는 냄새 나": "대피",
        "잠깐 5분만 장애인 칸에 대도 돼?": "주차할 수 없어요",
        "일반차인데 전기차 칸 비었으면 대도 돼?": "주차할 수 없어요",
        "전기차인데 일반 칸에 대도 돼?": "주차할 수 있어요",
        "그거 취소해줘": "어떤 요청을 취소할까요",
        "부산역 B-30 비었어?": "B-30 칸이 없어요",
        "김해공항 C-05 비었어?": "C-05 칸이 없어요",
        "넌 이름이 뭐니": "PARK:ON 주차 안내 챗봇",
        "너 누구야?": "PARK:ON 주차 안내 챗봇",
        "안녕하세요": "PARK:ON 주차 안내 챗봇",
        "What is your name?": "PARK:ON 주차 안내 챗봇",
    }
    PASS_THROUGH = (
        "장애인 주차 가능한 곳 어디야?", "전기차 자리 가능한 곳 있어?", "장애인 자리 이용 가능한 주차장?",
        "주차 연기 나중에 할게", "예약 연기 나중에", "B-06 비었어?", "부산역 A-03 비었어?",
        "김해공항 C-04 비었어?", "신세계 D-16 자리 있어?", "결제 취소해줘", "24번 차 어디야",
        "관리자 부르면 돈 들어?", "안녕 23번 차 어디야", "주차장 이름 알려줘",
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

    def test_red_light_becomes_alert_question(self):
        reply, rewritten = guard(_NoPending(), "빨간 불 깜빡이던데 괜찮은 거야?")
        self.assertIsNone(reply)
        self.assertEqual(rewritten, "지금 경보 있어?")

    def test_engine_uses_guard_before_routing(self):
        sid = uuid.uuid4().hex
        e.handle(sid, event="start")
        r = e.handle(sid, text="연기 나는 것 같아")
        self.assertEqual(r["meta"]["intent"], "emergency")
        self.assertNotIn("경보는 없어요", r["text"])
        r = e.handle(uuid.uuid4().hex, text="잠깐 5분만 장애인 칸에 대도 돼?")
        self.assertEqual(r["meta"]["intent"], "parking_permission")


if __name__ == "__main__":
    unittest.main()
