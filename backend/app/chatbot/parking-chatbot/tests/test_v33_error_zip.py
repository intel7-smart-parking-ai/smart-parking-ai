"""시연 서버 화면 오류(error.zip, 11/10)를 재현해 고정한다.

1) '개발원 주차장 주차요금 안내해줘'·'국내선 청사로 갈려고…'가 목적지 설정으로 가로채짐(대화 AI도 못 거침)
2) '김해공항 안 붐비는 시간대는?'에 구역별 빈자리로 답함
3) '3시에 혼잡하나?' → '오전인지 오후인지' → '오후'를 이어 받지 못함
4) '영화관으로 가는 가장 가까운 주차면'에 자료가 없다는 말 없이 빈자리만 답함
5) '전기차 충전 현황'(주차장 없음)이 말없이 김해공항으로 정해짐
"""
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from chatbot import config, engine as e, forecast_chat
from chatbot.adapters import MockAdapter
from chatbot.parser import parse_rules

KST = forecast_chat.KST


def fake_forecast(lot_id, at):
    """서버 예측(demo_history.forecast)과 같은 모양. 13시가 가장 한산, 18시가 가장 붐빔."""
    start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now(KST)
    hours = []
    for hour in range(24):
        slot = start + timedelta(hours=hour)
        if slot + timedelta(hours=1) <= now:
            continue
        ratio = 0.2 if hour == 13 else 0.9 if hour == 18 else 0.5
        hours.append({"at": slot.isoformat(), "empty": round(64 * (1 - ratio)), "capacity": 64,
                      "occupancy_percent": round(ratio * 100), "level": "여유" if ratio < 0.4 else "혼잡" if ratio > 0.8 else "보통"})
    selected = next(h for h in hours if datetime.fromisoformat(h["at"]).hour == at.hour)
    return {"lot_id": lot_id, "target": at.isoformat(), "selected": selected, "hours": hours, "is_mock": True,
            "source": "demo_history", "mae_spaces": 2.0}


class Base(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        for target, value in (("LLM_PROVIDER", "none"),):
            change = patch.object(config, target, value)
            change.start()
            self.addCleanup(change.stop)

    def ask(self, text, sid="z"):
        return e.handle(sid, text=text)


class DestinationRuleTests(Base):
    def test_fee_or_place_questions_are_not_destination(self):
        for text in ("개발원 주차장 주차요금 안내해줘", "김해공항 국내선 청사로 갈려고 하는데 가장 가까운 주차면이 어디야",
                     "공항 주차장 빈자리 안내해줘", "부산역 주차장으로 가려면 요금 얼마야?"):
            with self.subTest(text=text):
                self.assertNotEqual(parse_rules(text).intent, "set_destination")
        r = self.ask("개발원 주차장 주차요금 안내해줘")
        self.assertIn("1,000원", r["text"])
        self.assertNotIn("목적지", r["text"])

    def test_explicit_destination_still_works(self):
        for text in ("개발원 주차장으로 안내해줘", "공항 주차장으로 갈게", "신세계 주차장으로 가줘", "부산역 주차장으로 이동할게", "D로 안내해 줘", "B로 갈게"):
            with self.subTest(text=text):
                self.assertEqual(parse_rules(text).intent, "set_destination")
        self.assertEqual(parse_rules("목적지 취소").intent, "cancel_destination")


@patch.object(MockAdapter, "congestion_forecast", side_effect=fake_forecast)
class ForecastTests(Base):
    def test_best_time_uses_server_hours(self, _):
        r = self.ask("김해공항 내일 안 붐비는 시간대는?")
        self.assertEqual(r["meta"].get("forecast_mode"), "best_time", r)
        self.assertIn("오후 1시", r["text"])
        self.assertIn("오후 6시", r["text"])          # 가장 붐빌 시간
        self.assertIn("가상 시연 이력", r["text"])

    def test_clarification_follow_up_is_joined(self, _):
        first = self.ask("김해공항 3시에 혼잡하나?", sid="f")
        self.assertIn("오전인지 오후인지", first["text"])
        second = self.ask("오후", sid="f")
        self.assertIn("오후 3시", second["text"], second)
        self.assertIn("김해공항", second["text"])

    def test_unrelated_reply_drops_the_question(self, _):
        self.ask("김해공항 3시에 혼잡하나?", sid="g")
        self.assertNotIn("오후 3시", self.ask("고마워", sid="g")["text"])
        self.assertIsNone(e.SESSIONS["g"].forecast_ask)

    def test_expired_question_is_not_joined(self, _):
        self.ask("김해공항 3시에 혼잡하나?", sid="h")
        e.SESSIONS["h"].forecast_ask["expires"] = time.monotonic() - 1
        self.assertNotIn("오후 3시", self.ask("오후", sid="h")["text"])

    def test_current_crowd_is_not_forecast(self, _):
        for text in ("지금 공항 붐벼?", "덜 붐비는 곳 추천해줘"):
            with self.subTest(text=text):
                self.assertFalse(forecast_chat.wants(text))


class FacilityAndChargingTests(Base):
    def test_unknown_place_is_said_first(self):
        r = self.ask("신세계 백화점 주차장에서 영화관으로 가는 가장 가까운 주차면이 어디야?")
        self.assertTrue(r["text"].startswith("영화관은 아직 안내 자료에 없어요"), r["text"])

    def test_known_place_keeps_guide(self):
        r = self.ask("신세계 백화점 주차장에서 엘리베이터가 가장 가까운 주차면이 어디야")
        self.assertIn("엘리베이터", r["text"])
        self.assertNotIn("안내 자료에 없어요", r["text"])

    def test_topic_particle(self):
        self.assertEqual(e._topic_particle("영화관"), "영화관은")
        self.assertEqual(e._topic_particle("엘리베이터"), "엘리베이터는")

    def test_charging_without_lot_says_which(self):
        r = self.ask("전기차 충전 현황")
        self.assertIn("김해공항부터", r["text"])
        self.assertEqual(r["card"]["data"]["lot_id"], "C")
        self.assertNotIn("김해공항부터", self.ask("신세계 충전 현황", sid="d")["text"])



class PlaceGuardTests(Base):
    """실모델 확인(11/10): '영화관 방향 표시는 몰 쪽 A·C구역' — 안내 자료에 없는 장소의 위치를 지어냄(숫자 없음 → 다른 검사 통과)."""

    def test_near_place(self):
        from chatbot.info_gaps import near_place
        self.assertEqual(near_place("신세계 백화점 주차장에서 영화관으로 가는 가장 가까운 주차면이 어디야?"), "영화관")
        self.assertEqual(near_place("화장실이랑 가장 가까운 빈자리 어디야"), "화장실")
        self.assertIsNone(near_place("김해공항 국내선 청사로 갈려고 하는데 가장 가까운 주차면이 어디야"))
        self.assertIsNone(near_place("공항 주차장에서 가장 가까운 자리"))

    def test_knowledge_marks_missing_place(self):
        from chatbot.tools.read import ReadTools
        tools = ReadTools(e.data, e.current_alert)
        missing = tools.search_knowledge("신세계 백화점 주차장에서 영화관으로 가는 가장 가까운 주차면이 어디야?", ["D"])
        self.assertEqual(missing["data"].get("not_in_guide"), ["영화관"])
        known = tools.search_knowledge("신세계 백화점 주차장에서 엘리베이터가 가장 가까운 주차면이 어디야", ["D"])
        self.assertNotIn("not_in_guide", known["data"])

    def test_evidence_blocks_made_up_place_location(self):
        from chatbot.agents.evidence import check_answer
        q = "신세계 백화점 주차장에서 영화관으로 가는 가장 가까운 주차면이 어디야?"
        obs = [{"tool": "search_knowledge", "args": {}, "result": {"tool": "search_knowledge", "ok": True,
                "data": {"matches": [], "missing": True, "unregistered": [], "not_in_guide": ["영화관"]}, "source": {}}}]
        made_up = "시설 안내에 따르면 영화관 방향 표시는 몰 쪽 A·C구역 위에 있어요."
        honest = "영화관 위치는 안내 자료에 없어요. 지금 빈자리는 화면에서 확인해 주세요."
        self.assertEqual(check_answer(made_up, q, obs), "unsupported_place")
        self.assertNotEqual(check_answer(honest, q, obs), "unsupported_place")

    def test_prefetch_reads_guide_for_nearest(self):
        from test_v33_prefetch import prefetched
        names = [name for name, _ in prefetched("신세계 백화점 주차장에서 영화관으로 가는 가장 가까운 주차면이 어디야?")]
        self.assertIn("search_knowledge", names)
        self.assertIn("parking_status", names)
        names = [name for name, _ in prefetched("김해공항 국내선 청사로 갈려고 하는데 가장 가까운 주차면이 어디야")]
        self.assertIn("search_knowledge", names)


@patch.object(MockAdapter, "congestion_forecast", side_effect=fake_forecast)
class PartOfDayTests(Base):
    def test_morning_uses_representative_hour(self, _):
        r = self.ask("내일 아침에 가면 신세계 자리 있어?")
        self.assertIn("아침(오전 9시 기준)", r["text"], r)


class ForeignTimingTests(unittest.TestCase):
    """영어 질문이 거의 항상 AI 시간 초과였다(질문 번역 시간까지 4초에 포함). 화면 제한 20초 안에서 다시 나눈다."""

    def test_budget_fits_screen_limit(self):
        from chatbot import http_router as h
        worst = h.QUESTION_TRANSLATE_SEC + h.FOREIGN_TURN_SEC + 8.0   # 답 번역은 최대 8초(response_locale)
        self.assertLessEqual(max(worst, h.ANSWER_DEADLINE_SEC), 18.0)
        self.assertGreaterEqual(h.FOREIGN_TURN_SEC, 5.0)              # 미리 조회 뒤 모델 1회(평균 2~3초) + 고쳐 쓰기 여유


if __name__ == "__main__":
    unittest.main()
