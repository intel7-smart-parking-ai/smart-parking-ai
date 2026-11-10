"""혼잡도 예측 C+안 — 챗봇은 서버 예측 조회 결과만으로 답한다 (11/09)."""
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from chatbot import engine as e, forecast_chat


def fake_forecast(lot_id, at):
    return {"lot_id": lot_id, "target": at.isoformat(), "is_mock": True, "source": "demo_history",
            "label": "가상 시연 이력 기준 예측", "mae_spaces": 3.2, "validation_samples": 168,
            "selected": {"at": at.replace(minute=0).isoformat(), "empty": 12, "capacity": 64,
                         "occupancy_percent": 81, "level": "보통"},
            "hours": []}


class ForecastChatTests(unittest.TestCase):
    def ask(self, text):
        return e.handle(uuid.uuid4().hex, text=text)

    def test_detects_forecast_questions_only(self):
        self.assertTrue(forecast_chat.wants("내일 오후 3시 공항 붐벼?"))
        self.assertTrue(forecast_chat.wants("토요일 신세계 자리 있을까?"))
        self.assertFalse(forecast_chat.wants("공항 빈자리 몇 개야?"))
        self.assertFalse(forecast_chat.wants("23번 차 어디야"))
        self.assertFalse(forecast_chat.wants("지금 붐벼?"))
        self.assertFalse(forecast_chat.wants("덜 붐비는 곳 추천해줘"))
        self.assertFalse(forecast_chat.wants("비행기가 2시20분 출발인데 3시간전에 도착해도 혼잡하지 않을까?"))

    def test_time_parsing_never_guesses_am_pm(self):
        now = datetime(2026, 11, 9, 10, 0, tzinfo=forecast_chat.KST)
        target, _ = forecast_chat.parse_when("내일 오후 3시", now)
        self.assertEqual((target.day, target.hour), (10, 15))
        target, missing = forecast_chat.parse_when("내일 3시", now)
        self.assertIsNone(target); self.assertEqual(missing, "오전오후")
        target, _ = forecast_chat.parse_when("내일 15시 반", now)
        self.assertEqual((target.hour, target.minute), (15, 30))

    def test_answer_uses_server_forecast_and_card(self):
        with patch.object(e.data, "congestion_forecast", side_effect=fake_forecast, create=True):
            r = self.ask("내일 오후 3시 공항 붐벼?")
        self.assertEqual(r["meta"]["intent"], "congestion_forecast")
        self.assertEqual(r["card"]["type"], "congestion_forecast")
        self.assertIn("약 12자리", r["text"])
        self.assertIn("가상 시연 이력 기준", r["text"])

    def test_no_numbers_without_server_forecast(self):
        with patch.object(e.data, "congestion_forecast", side_effect=RuntimeError("down"), create=True):
            r = self.ask("내일 오후 3시 신세계 혼잡해?")
        self.assertIn("조회하지 못했어요", r["text"])
        self.assertIsNone(r["card"])

    def test_scope_and_clarification(self):
        self.assertIn("김해공항·신세계", self.ask("내일 오후 3시 부산역 붐벼?")["text"])
        self.assertIn("오전인지 오후인지", self.ask("내일 3시 공항 붐벼?")["text"])
        self.assertIn("일주일", self.ask("11월 30일 오후 2시 공항 혼잡해?")["text"])


if __name__ == "__main__":
    unittest.main()
