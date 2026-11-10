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


class BotReview1515Tests(Base):
    """봇 점검(11/10 15:15) PR #95 🟠·🟡 항목."""

    def test_ahead_minutes(self):
        from chatbot import plan
        self.assertEqual(plan.ahead_minutes("6172 차 60분 뒤에 나가면 얼마야?"), 60)
        self.assertEqual(plan.ahead_minutes("1시간 반 뒤"), 90)
        self.assertEqual(plan.ahead_minutes("30분 더 있으면"), 30)
        self.assertIsNone(plan.ahead_minutes("3시간이면?"))

    def test_virtual_car_future_fee(self):
        r = self.ask("1724 차 60분 뒤에 나가면 얼마야?")
        self.assertIn("1시간 뒤에 나가면 예상 요금은", r["text"])
        self.assertIn("실제 금액은 출차·결제 때 서버가 정해요", r["text"])

    def test_real_car_future_fee_without_ai(self):
        r = self.ask("34번 차 60분 뒤 얼마?", sid="real")
        self.assertIn("1시간 뒤에 나가면 예상 요금은", r["text"], r["meta"].get("route"))

    def test_admin_call_keeps_virtual_vehicle(self):
        self.ask("내차 1724 위치", sid="adm")
        self.ask("스크래치 났어", sid="adm")
        r = self.ask("관리자 불러줘", sid="adm")
        call = r["card"]["data"]
        self.assertIsNone(call["car_id"])
        self.assertEqual(call["plate_number"], "17가1724")
        self.assertIn("17가1724 차량(가상)", call["summary"])
        self.assertNotIn("차량 번호 미확인", call["summary"])

    def test_language_guess_without_language(self):
        from chatbot.http_router import _guess_language
        self.assertEqual(_guess_language("Is there parking at Gimhae Airport?"), "en")
        self.assertEqual(_guess_language("金海机场有车位吗"), "zh-CN")
        self.assertEqual(_guess_language("空港に空きはありますか"), "ja")
        for text in ("공항 자리 있어?", "OK", "EV", "23"):
            self.assertEqual(_guess_language(text), "ko", text)

    def test_cheapest_between_named_lots_only(self):
        r = self.ask("역이랑 공항 중 어디가 싸?", sid="rank")
        self.assertNotIn("개발원", r["text"])
        self.assertNotIn("신세계", r["text"])

    def test_translation_unit_duplicates_removed(self):
        # 개수 자리표시(__COUNT0__)는 '34 spaces'처럼 단위까지 돌려준다. 번역이 단위를 또 붙여도 한 번만 남긴다.
        from chatbot import response_locale as rl
        token = "__COUNT0__"
        for translated, value, want in (("There are __COUNT0__ spaces.", "34 spaces", "There are 34 spaces."),
                                        ("有__COUNT0__ 个空车位。", "33个车位", "有33个车位。"),
                                        ("__COUNT0__ 台あります", "5台", "5台あります"),
                                        ("There are __COUNT0__ left.", "34 spaces", "There are 34 spaces left."),
                                        ("There are __COUNT0__ vacant spaces.", "24 spaces", "There are 24 vacant spaces.")):
            with self.subTest(translated=translated):
                self.assertEqual(rl._restore(translated, token, {token: value}), want)
        # 봇 실측 문장: 'There are 24 spaces vacant spaces out of 64 spaces'
        two = rl._restore("There are __COUNT0__ vacant spaces out of __COUNT1__.", "__COUNT0____COUNT1__",
                          {"__COUNT0__": "24 spaces", "__COUNT1__": "64 spaces"})
        self.assertEqual(two, "There are 24 vacant spaces out of 64 spaces.")


class DamageAndHoursTests(Base):
    """실모델 재확인(11/10): '스크래치 났어'가 AI 시간 초과 → '이해하지 못했어요', 운영 시간 답이 자료 없는 주차장 언급으로 탈락."""

    def test_damage_goes_to_admin_notice(self):
        for text in ("스크래치 났어", "차가 긁혔어요", "누가 내 차 박았어"):
            with self.subTest(text=text):
                r = self.ask(text, sid=text)
                self.assertIn("관리자", r["text"])
                self.assertNotIn("이해하지 못했어요", r["text"])
        self.assertNotIn("관리자가 직접 확인", self.ask("차에서 연기 나", sid="smoke")["text"])

    def test_no_data_sentence_may_name_unread_lots(self):
        from chatbot.agents.evidence import check_answer
        q = "주차장 몇 시까지 해?"
        obs = [{"tool": "search_knowledge", "args": {}, "result": {"tool": "search_knowledge", "ok": True, "data": {
            "matches": [{"lot_id": "C", "topic": "hours", "text": "시연 기준 매일 24시간 입·출차할 수 있어요."}],
            "missing": False, "unregistered": []}, "source": {}}}]
        honest = ("김해공항 주차장은 시연 기준 매일 24시간 이용할 수 있어요.\n"
                  "개발원주차장과 부산역주차장은 운영 시간 자료가 없어요.")
        self.assertNotEqual(check_answer(honest, q, obs), "unsupported_lot")
        claim = "김해공항 주차장은 시연 기준 매일 24시간 이용할 수 있어요. 개발원주차장은 지금 비어 있어요."
        self.assertEqual(check_answer(claim, q, obs), "unsupported_lot")


class GemmaTimeoutTests(Base):
    """봇 점검(11/10, gemma): '김해공항 3일 얼마?'가 3.1초에서 끊김 — 모델 연결 자체가 LLM_TIMEOUT_SEC(3초)라
    답 쓰기 제한(LLM_ANSWER_TIMEOUT_SEC 5초)이 적용되지 않았다."""

    def test_client_timeout_covers_answer_limit(self):
        import importlib.util
        if importlib.util.find_spec("langchain_openai") is None:
            self.skipTest("langchain_openai 없음")
        from chatbot.agents import conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_TIMEOUT_SEC", 3.0), \
                patch.object(config, "LLM_ANSWER_TIMEOUT_SEC", 5.0), patch.object(config, "api_key", return_value="test-only"), \
                patch.object(config, "LLM_MODEL", "google/gemma-4-26b-a4b-it"):
            model = conversation._provider_model(conversation.AgentOptions())
        self.assertGreaterEqual(float(model.request_timeout), 5.0)

    def test_three_days_price_is_duration(self):
        from chatbot import plan
        self.assertEqual(plan.make("김해공항 3일 얼마?").minutes, 4320)
        self.assertIsNone(plan.make("11월 3일에 가면 자리 있어?").minutes)

    def test_vacancy_where_is_not_find_my_car(self):
        r = self.ask("장애인 주차 어디가 비었어?", sid="acc")
        self.assertIn("장애인", r["text"])
        self.assertNotIn("차량 번호를 알려주세요", r["text"])


if __name__ == "__main__":
    unittest.main()
