"""내부 검사(실모델 125턴)와 연결성 점검에서 나온 문제들 (11/11~11/12).

1) "23번 차의 입차 기록이 없고, 번호 미인식 차량이 68대 있어요" — 가상 주차장(김해공항·신세계) 차량은 번호판으로 식별해
   car_id가 항상 비어 온다. 그 차들을 '번호 미인식'으로 셌다.
2) 차를 못 찾았을 때 대화 AI의 맞는 답("23번 차량의 입차 기록을 찾지 못했어요")이 근거 없음으로 버려졌다(14턴).
3) 가상 차량을 못 찾은 복합 질문("1724 차 찾아주고 신세계 빈자리도 알려줘")에서 AI 답이 버려지고 규칙 답은 빈자리만 답했다.
4) 질문을 다르게 이해: "D로 가면 얼마나 걸려?"에 요금, "충전기 몇 대나 쓰고 있어?"에 빈 충전 자리 수.
5) 연결성: 화면의 '이용 방법' 버튼이 빈자리 답을 냈고, 한국어 화면에서 'EV'만 쳐도 영어로 답했다.
"""
import unittest
from unittest.mock import patch

from chatbot import agents, charging_intent, config, conversation_turn, engine as e, parser, plan, response_locale as locale
from chatbot.adapters import _sticker_lot
from chatbot.agents.evidence import REPAIR_HINTS, check_answer
from chatbot.tools.read import ReadTools


def obs(tool, result):
    return {"tool": tool, "call_id": tool, "result": result}


class VirtualCarsAreNotUnrecognizedTests(unittest.TestCase):
    def test_only_sticker_lots_count_unrecognized_cars(self):
        self.assertTrue(_sticker_lot("A"))
        self.assertTrue(_sticker_lot("B"))
        self.assertFalse(_sticker_lot("C"))       # 김해공항: 번호판으로 식별
        self.assertFalse(_sticker_lot("D"))       # 신세계: 번호판으로 식별

    def test_api_snapshot_leaves_virtual_lot_cars_out(self):
        from test_api_contract import ApiContractTests
        fixture = ApiContractTests("test_04_null_session_no_fee_request")
        fixture.setUp()                            # 가짜 서버: A·B·C 각 4칸, 첫 칸은 car_id 없이 주차 중
        self.addCleanup(fixture.tearDown)
        snapshot = fixture.adapter.get_empty_spots()
        lots = {car["lot_id"] for car in snapshot["unrecognized"]}
        self.assertEqual(lots, {"A", "B"})         # 전에는 가상 주차장 C의 차도 '번호 미인식'으로 들어 있었다


class AbsentVehicleTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def absent(self, car="99"):
        result = ReadTools(e.data, e.current_alert, vehicle_provider=e.data.find_car, allowed_car_id=car).vehicle_info(car)
        self.assertFalse(result["ok"])
        self.assertIs(result["data"].get("not_found"), True)
        return result

    def test_lookup_failure_is_not_marked_as_not_found(self):
        tools = ReadTools(e.data, e.current_alert, vehicle_provider=lambda car: {"unavailable": True}, allowed_car_id="99")
        self.assertNotIn("not_found", tools.vehicle_info("99", include_fee=False)["data"])     # 조회 실패 ≠ 없음

    def test_not_found_answer_passes_without_other_evidence(self):
        seen = [obs("vehicle_info", self.absent())]
        for answer in ("99번 차량의 입차 기록을 찾지 못했어요. 차량 번호를 다시 한번 확인해 주시겠어요?",
                       "99번 차량의 입차 기록이 없어서 현재 요금을 확인할 수 없어요.",
                       "99번 차량은 입차 기록이 확인되지 않아요."):
            self.assertIsNone(check_answer(answer, "99번 차 어디 있어?", seen), answer)

    def test_not_found_does_not_license_other_claims(self):
        seen = [obs("vehicle_info", self.absent())]
        for answer in ("99번 차량은 개발원주차장 A-03에 있어요.",                               # 위치를 지어냄
                       "99번 차량의 입차 기록을 찾지 못했어요. 대신 김해공항 주차장에 62자리가 있어요.",   # 조회 안 한 빈자리
                       "99번 차량의 입차 기록을 찾지 못했어요. 지금까지 요금은 3,000원이에요.",
                       "99번 차량의 입차 기록을 찾지 못해서 관리자를 호출했어요.",
                       "출차나 차단기 개방은 제가 직접 해드릴 수 없어요."):                      # 못 찾았다는 말이 없다
            self.assertEqual(check_answer(answer, "99번 차 어디 있어?", seen), "no_evidence", answer)

    def test_compound_answer_with_an_absent_virtual_car(self):
        tools = ReadTools(e.data, e.current_alert, virtual_numbers=("0001",))
        missing = tools.virtual_vehicle_info("0001")
        self.assertIs(missing["data"].get("not_found"), True)
        status = tools.parking_status(["D"])
        empty = status["data"]["rows"][0]["empty"]
        seen = [obs("virtual_vehicle_info", missing), obs("parking_status", status)]
        good = f"신세계백화점주차장은 현재 빈자리가 {empty}곳 있어요.\n\n말씀하신 0001 차량은 저희 주차장에서 찾을 수 없어요."
        self.assertIsNone(check_answer(good, "0001 차 찾아주고 신세계 빈자리도 알려줘", seen))       # 전에는 unsupported_number
        for bad in (f"0001 차량은 신세계백화점주차장에 있어요. 빈자리는 {empty}곳이에요.",               # 못 찾은 차의 위치를 말함
                    f"0001 차량은 찾지 못했지만 B-12에 있을 거예요. 빈자리는 {empty}곳이에요."):
            self.assertIsNotNone(check_answer(bad, "0001 차 찾아주고 신세계 빈자리도 알려줘", seen), bad)
        self.assertIn("not_found", REPAIR_HINTS["absent_vehicle_claim"])

    def test_the_absent_number_as_a_count_is_not_a_vehicle_mention(self):
        # 못 찾은 차 번호와 같은 숫자가 빈자리 수로 나와도 '그 차를 말한 문장'으로 보지 않는다
        tools = ReadTools(e.data, e.current_alert, vehicle_provider=e.data.find_car, allowed_car_id="62")
        missing = tools.vehicle_info("62")
        status = ReadTools(e.data, e.current_alert).parking_status(["C"])
        self.assertEqual(status["data"]["rows"][0]["empty"], 62)
        answer = "62번 차량의 입차 기록을 찾지 못했어요.\n김해공항 주차장에는 62자리가 있어요."
        self.assertIsNone(check_answer(answer, "62번 차 어디 있고 공항 자리 있어?",
                                       [obs("vehicle_info", missing), obs("parking_status", status)]))

    def test_prefetch_hands_over_the_confirmed_absence(self):
        session = e.get_session("p1")
        tools = ReadTools(e.data, e.current_alert, vehicle_provider=e.data.find_car, allowed_car_id="99")
        done = conversation_turn._prefetch("99번 차 어디 있어?", session, tools, "99")
        self.assertEqual([row["tool"] for row in done], ["vehicle_info"])
        self.assertIs(done[0]["result"]["data"]["not_found"], True)

    def test_absent_vehicle_is_not_a_missing_lookup(self):
        query = conversation_turn.interpret("99번 차 어디 있어?", e.get_session("p2"))
        self.assertTrue(conversation_turn._missing(query, []))                       # 조회가 없으면 '빠진 조회'
        self.assertEqual(conversation_turn._missing(query, [], absent={"99"}), "")   # 없다고 확인됐으면 빠진 것이 아니다

    def test_simple_question_about_an_absent_car_skips_the_model(self):
        import importlib.util
        if importlib.util.find_spec("langchain") is None:
            self.skipTest("optional LangChain not installed")       # 없으면 대화 AI 자체가 꺼져 있다
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=AssertionError("모델을 부르면 안 된다")):
            r = e.handle("s1", text="99번 요금 얼마야?")
        self.assertIn("99번 차의 입차 기록이 없어요", r["text"])
        self.assertIn("agent:skip", r["meta"]["route"])

    def test_compound_question_still_goes_to_the_model(self):
        self.assertTrue(conversation_turn._simple_vehicle_turn("99번 차 어디 있어?"))
        self.assertFalse(conversation_turn._simple_vehicle_turn("99번 차 어디 있고 공항 자리는 있어?"))
        self.assertFalse(conversation_turn._simple_vehicle_turn("23번이랑 41번 요금 비교해줘"))


class MisreadQuestionTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_how_long_does_it_take_is_not_a_fee_question(self):
        for text in ("D로 가면 얼마나 걸려?", "공항까지 몇 분 걸려?", "주차하는 데 얼마 걸려?"):
            self.assertNotIn("fee", plan.for_text(text).wants, text)
            self.assertNotIn(parser.parse_rules(text).intent, ("get_fee", "fee_rule"), text)
            self.assertNotIn("차량 번호를 알려주세요", e.handle("m" + text, text=text)["text"], text)

    def test_no_travel_time_answer_may_go_out_without_a_lookup(self):
        from chatbot.agents.evidence import no_tool_reply_ok
        answer = ("신세계백화점주차장으로 가는 길이나 소요 시간은 안내 자료에 없어요. "
                  "대신 지금 신세계백화점주차장의 빈자리나 요금 정보를 알려드릴까요?")
        self.assertTrue(no_tool_reply_ok("D로 가면 얼마나 걸려?", answer))
        self.assertFalse(no_tool_reply_ok("D 요금 얼마야?", answer))                       # 요금 질문에는 조회가 필요하다
        self.assertFalse(no_tool_reply_ok("D로 가면 얼마나 걸려?", "신세계백화점주차장까지 10분 걸려요."))   # 지어낸 시간

    def test_fee_questions_are_still_fee_questions(self):
        for text in ("요금 얼마나 나와?", "3시간이면 얼마야?", "요금이 얼마나 걸려 있어?", "23번 요금 얼마야?"):
            self.assertIn(parser.parse_rules(text).intent, ("get_fee", "fee_rule"), text)

    def test_chargers_in_use_opens_the_charging_status(self):
        for text in ("신세계 충전기 지금 몇 대나 쓰고 있어?", "충전기 사용 중인 거 몇 개야?", "충전기 이용 중이야?"):
            self.assertTrue(charging_intent.wants_status(text), text)
        r = e.handle("c1", text="신세계 충전기 지금 몇 대나 쓰고 있어?")
        self.assertEqual((r.get("card") or {}).get("type"), "charging_status")
        for text in ("충전 구역 빈자리 알려줘", "전기차 충전 자리 있어?", "충전 칸 남은 자리 몇 개야?"):   # 빈자리 질문은 그대로
            self.assertFalse(charging_intent.wants_status(text), text)


class ConnectionTests(unittest.TestCase):
    """화면(frontend/src/ChatPanel.jsx)의 빠른 질문 버튼 여섯 개가 보내는 문장."""
    BUTTONS = {"빈자리 확인": "주차장별 빈자리 몇 개 있어?", "내 차 찾기": "내 차 위치를 찾고 싶어요", "주차요금": "주차요금 안내해줘",
               "전기차 구역": "충전 구역 빈자리 알려줘", "장애인 구역": "장애인 구역 빈자리 알려줘", "이용 방법": "주차장 이용 방법 알려줘"}

    def setUp(self):
        e.reset_all()
        locale.load_seed()
        self.addCleanup(e.reset_all)

    def answer(self, label):
        return e.handle("b" + label, text=self.BUTTONS[label])

    def test_every_quick_button_gets_the_answer_its_label_promises(self):
        self.assertIn("전체 주차장 빈자리는 총", self.answer("빈자리 확인")["text"])
        self.assertIn("차량 번호를 알려주세요", self.answer("내 차 찾기")["text"])
        self.assertIn("주차장별 요금표예요.", self.answer("주차요금")["text"])
        self.assertIn("충전 구역 빈자리는 총", self.answer("전기차 구역")["text"])
        self.assertIn("장애인 구역 빈자리는 총", self.answer("장애인 구역")["text"])
        usage = self.answer("이용 방법")
        self.assertTrue(usage["text"].startswith("이용 방법을 알려 드릴게요."))
        self.assertNotIn("빈자리는 총", usage["text"])                  # 전에는 빈자리 답과 지도가 나갔다
        self.assertIsNone(usage.get("card"))

    def test_usage_guide_variants_and_translation(self):
        for text in ("이용 방법 알려줘", "어떻게 이용해?", "주차장 이용 안내", "사용 방법 좀 알려줘"):
            r = parser.parse_rules(text)
            self.assertEqual((r.intent, r.params.get("usage_guide")), ("help", True), text)
        self.assertNotEqual(parser.parse_rules("전기차 구역 빈자리 알려줘").intent, "help")
        text = self.answer("이용 방법")["text"]
        with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
            for language in locale.LANGUAGES:
                self.assertIsNotNone(locale._translate_known((text,), language), language)

    def test_usage_guide_skips_the_model(self):
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=AssertionError("모델을 부르면 안 된다")):
            r = self.answer("이용 방법")
        self.assertIn("agent:skip", r["meta"]["route"])

    def test_short_latin_input_on_a_korean_screen_stays_korean(self):
        for text in ("EV", "ok", "OK", "hi", "A-01", "okay", "EV 자리", "23"):
            self.assertEqual(locale.resolve_language("ko", text), "ko", text)
        for text in ("Where can I park?", "EV charging", "Parking fee?", "Where is my car 1724?", "yes"):
            self.assertEqual(locale.resolve_language("ko", text), "en", text)
        self.assertEqual(locale.resolve_language("ja", "EV"), "ja")       # 화면에서 고른 언어는 그대로


if __name__ == "__main__":
    unittest.main()


class FullCheckFindingsTests(unittest.TestCase):
    """11/12 전수검사(로컬 서버에서 챗봇 답·카드를 서버 값과 대조)에서 나온 것. 숫자·합계·카드·요금표는 모두 일치했고,
    아래는 답이 묻는 것에 못 미치던 곳이다."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_exit_question_about_a_sticker_car_gets_payment_guidance(self):
        # 전에는 "현재 요금은 1,000원이에요."로 끝났다(가상 차량은 결제·출차 안내가 붙는데 실물 차량은 없었다)
        r = e.handle("x1", text="23번 출차해도 돼?")
        self.assertRegex(r["text"], r"\d[\d,]*원")
        self.assertIn("출차 전에 요금을 먼저 결제해 주세요.", r["text"])
        r = e.handle("x2", text="23번 요금 얼마야?")                     # 요금만 물으면 붙지 않는다
        self.assertNotIn("출차 전에", r["text"])

    def test_exit_question_after_naming_my_car(self):
        e.handle("x3", text="23번 차 어디야?")
        self.assertIn("출차 전에 요금을 먼저 결제해 주세요.", e.handle("x3", text="나가도 돼?")["text"])

    def test_follow_up_about_a_found_virtual_car(self):
        first = e.handle("v1", text="1724 어디 있어?")["text"]
        self.assertIn("17가1724", first)
        for text in ("그 차 요금 얼마야?", "그 차 어디 있다고?"):
            r = e.handle("v1", text=text)
            self.assertIn("17가1724", r["text"], text)
            self.assertNotIn("차량 번호를 알려주세요", r["text"], text)
        r = e.handle("v1", text="그 차 나가도 돼?")
        self.assertIn("17가1724", r["text"])
        self.assertIn("출차 전에 요금을 먼저 결제해 주세요.", r["text"])

    def test_that_car_without_any_earlier_car_still_asks(self):
        r = e.handle("v2", text="그 차 요금 얼마야?")
        self.assertIn("차량 번호를 알려주세요", r["text"])
        r = e.handle("v3", text="그 차 나가도 돼?")
        self.assertIn("차량 번호를 알려 주시면", r["text"])

    def test_new_lines_are_translated_without_the_model(self):
        from chatbot import templates
        locale.load_seed()
        with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
            for line in (templates.EXIT_UNPAID, templates.EXIT_PAID):
                for language in locale.LANGUAGES:
                    self.assertIsNotNone(locale._translate_known((line,), language), (language, line))

    def test_where_again_about_my_car(self):
        # 실모델에서도 "어디 있다고?"의 AI 답이 버려지고 "잘 이해하지 못했어요"가 나갔다
        e.handle("w1", text="23번 차 어디야?")
        r = e.handle("w1", text="어디 있다고?")
        self.assertIn("23번 차", r["text"])
        self.assertRegex(r["text"], r"[A-D]-\d{2}")
        self.assertIn("잘 이해하지 못했어요", e.handle("w2", text="어디 있다고?")["text"])    # 말한 차가 없으면 그대로

    def test_foreign_quick_buttons_go_back_to_the_korean_button_sentence(self):
        # 영어·일본어 '주차요금' 버튼 문장이 모델 번역으로 "주차요금 알려줘"가 되어 차량 번호를 물었다
        with patch.object(locale, "_question_to_korean", side_effect=AssertionError("모델을 부르면 안 된다")):
            for korean, texts in locale._QUICK_BUTTON_TEXTS.items():
                for language, foreign in zip(("en", "zh-CN", "ja"), texts):
                    self.assertEqual(locale.question_to_korean_noted(foreign, language), (korean, None), foreign)
            self.assertEqual(locale.question_to_korean_noted("show me the parking fees", "en")[0], "주차요금 안내해줘")
        # 화면이 보내는 문장과 같은지(프론트 번역 파일이 바뀌면 여기서 걸린다)
        from pathlib import Path
        source = Path(__file__).resolve().parents[5] / "frontend" / "src" / "serviceTranslations.js"
        if source.exists():
            text = source.read_text(encoding="utf-8")
            for texts in locale._QUICK_BUTTON_TEXTS.values():
                for foreign in texts:
                    self.assertIn(foreign, text)

    def test_foreign_fee_button_shows_the_fee_table(self):
        import importlib.util
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("FastAPI not installed")
        from fastapi.testclient import TestClient
        from server_fastapi import app
        client = TestClient(app)
        for language, sentence, expected in (("en", "Show me the parking fees.", "Here are the parking fees for each lot."),
                                             ("ja", "駐車料金を教えてください。", "駐車場ごとの料金表です。"),
                                             ("zh-CN", "请介绍停车费用。", "以下是各停车场的收费标准。")):
            r = client.post("/api/v1/chat", json={"session_id": "fb-" + language, "text": sentence, "language": language}).json()
            self.assertIn(expected, r["text"], language)

    def test_am_pm_is_decided_from_the_korean_text(self):
        masked, replacements = locale._mask("오전 10시 37분에 들어왔어요.", "en")
        out = locale._restore("It entered at __NUMBER0__:__NUMBER1__ AM/PM.", masked, replacements)
        self.assertEqual(out, "It entered at 10:37 AM.")
        masked, replacements = locale._mask("오후 3시 20분에 들어왔어요.", "en")
        self.assertEqual(locale._restore("It entered at __NUMBER0__:__NUMBER1__ AM/PM.", masked, replacements), "It entered at 3:20 PM.")

    def test_ask_vehicle_answer_is_translated_into_japanese_too(self):
        from chatbot import templates
        locale.load_seed()
        with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
            for language in locale.LANGUAGES:
                self.assertIsNotNone(locale._translate_known((templates.ASK_VEHICLE,), language), language)
