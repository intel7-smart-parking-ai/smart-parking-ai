"""[실험] 되묻기는 주장이 아니다: 조회 없이 사용자에게 묻기만 하는 답은 근거 검사(no_evidence)에 걸리지 않는다.

11/11 새 문장 시험에서 대화 AI가 쓴 멀쩡한 되묻기('어디로 가시는지 알려주시면…')가 '근거 없음'으로 버려지고,
같은 답을 한 번 더 쓰게 한 뒤(모델 호출 2번) 엉뚱한 규칙 답('차량 번호를 알려주세요')으로 떨어졌다.
숫자·칸·상태·비교·실행 주장은 그대로 막는다. 차량 번호를 묻는 답은 규칙이 번호 입력 흐름을 열어야 하므로 제외한다.
"""
import unittest

from chatbot.agents.evidence import no_tool_reply_ok


class AskBackTests(unittest.TestCase):
    def test_plain_question_back_to_the_user_passes(self):
        cases = [
            ("내 차 어디에 세워야 해?", "어느 주차장에 주차하실 예정인가요? 목적지를 알려주시면 지금 빈자리가 있는 곳을 찾아 드릴게요."),
            ("차 어디에 대는 게 좋아?", "어디로 가시는지 목적을 알려주시면 딱 맞는 주차장을 추천해 드릴게요.\n\n"
                                  "· 여행이나 출국을 위해 김해공항에 가시나요?\n· 기차를 타러 부산역에 가시나요?\n"
                                  "· 쇼핑을 위해 신세계백화점에 가시나요?\n· 교육원 방문을 위해 개발원주차장을 찾으시나요?\n\n"
                                  "목적을 말씀해 주시면 지금 바로 빈자리와 요금을 확인해 드릴게요."),
            ("다음 주에 요금 올라?", "미래의 주차 요금이 어떻게 변할지는 미리 예측할 수 없어요.\n\n출발하시기 전에 다시 한번 확인해 주시면 "
                               "지금 기준으로 정확한 정보를 알려 드릴게요. 지금 궁금하신 주차장이 있다면 말씀해 주세요."),
        ]
        for request, answer in cases:
            self.assertTrue(no_tool_reply_ok(request, answer), answer[:40])

    def test_claims_hidden_in_a_question_are_still_blocked(self):
        cases = [
            ("자리 있어?", "네, 자리 넉넉해요. 어디로 가세요?"),
            ("어디가 싸?", "개발원이 제일 싼데 거기로 갈까요?"),
            ("어디가 싸?", "김해공항이랑 신세계 중에 신세계 쪽이 싼데 그쪽으로 갈까요?"),
            ("자리 있어?", "공항으로 가실래요, 신세계로 가실래요? 둘 다 자리 있어요."),
            ("어디가 좋아?", "공항이 한가한데 공항으로 가실래요, 신세계로 가실래요?"),
            ("요금 얼마야?", "요금은 30분에 2000원이에요. 어디로 가세요?"),
            ("어디 세워?", "B-06에 세우시겠어요?"),
            ("공항 자리 있어?", "공항은 지금 만차인가요?"),
            ("공항 자리 있어?", "공항 주차장으로 가시나요?"),               # 한 곳만 짚어 묻는 것은 조회가 필요한 질문을 피한 것
            ("결제해줘", "결제해 드렸어요. 더 필요한 게 있으세요?"),
            ("신세계로 가자", "신세계백화점주차장으로 안내해 드릴게요.\n\n지금 신세계백화점주차장의 빈자리를 확인해 드릴까요?"),
        ]
        for request, answer in cases:
            self.assertFalse(no_tool_reply_ok(request, answer), answer[:40])

    def test_asking_for_the_vehicle_number_stays_with_the_rule(self):
        self.assertFalse(no_tool_reply_ok("우리 차 어디 있는지 알려줘", "차량 번호 두 자리를 알려주시면 바로 위치를 찾아드릴게요."))

    def test_earlier_behaviour_is_unchanged(self):
        self.assertTrue(no_tool_reply_ok("고마워", "천만에요! 또 궁금한 게 있으면 물어보세요."))
        self.assertTrue(no_tool_reply_ok("오늘 날씨 어때?", "날씨 정보는 없어요. 빈자리나 요금을 물어봐 주세요."))
        self.assertFalse(no_tool_reply_ok("자리 있어?", "네, 자리 넉넉해요."))
        self.assertFalse(no_tool_reply_ok("안녕", "안녕하세요! 지금 공항은 한산해요."))
        self.assertFalse(no_tool_reply_ok("오늘 어때", "지금 자리가 넉넉해요."))


if __name__ == "__main__":
    unittest.main()


class PlainGoCommandTests(unittest.TestCase):
    """'신세계로 가자'처럼 가는 말로만 된 문장은 주차장 별칭을 읽어 목적지로 정한다(전에는 '이해하지 못했어요')."""

    def test_go_command_with_a_nickname_sets_destination(self):
        from chatbot.parser import parse
        for text, lot in (("신세계로 가자", "D"), ("공항으로 갈게", "C"), ("부산역으로 가줘", "B"), ("개발원으로 이동할게", "A")):
            result = parse(text, allow_llm=False)
            self.assertEqual((result.intent, result.params.get("lot_id")), ("set_destination", lot), text)

    def test_questions_about_the_way_are_not_commands(self):
        from chatbot.parser import parse
        for text in ("부산역으로 가는 버스 있어?", "신세계로 가도 돼?", "공항으로 가는 길 막혀?", "역으로 가자"):
            self.assertNotEqual(parse(text, allow_llm=False).intent, "set_destination", text)

    def test_engine_answers_the_command(self):
        from chatbot import engine as e
        e.reset_all()
        self.addCleanup(e.reset_all)
        self.assertIn("목적지로 정했어요", e.handle("go", text="신세계로 가자")["text"])


class ParkWhereFallbackTests(unittest.TestCase):
    """모델 없이(또는 AI 답이 버려진 뒤) 규칙이 답할 때: 세울 곳을 묻는 말에 차량 번호를 되묻지 않는다."""

    def setUp(self):
        from chatbot import engine as e
        e.reset_all()
        self.addCleanup(e.reset_all)
        self.e = e

    def test_where_to_park_gets_vacancy_not_a_vehicle_prompt(self):
        for i, text in enumerate(("차 어디에 대는 게 좋아?", "내 차 어디에 세워야 해?")):
            answer = self.e.handle(f"pw{i}", text=text)["text"]
            self.assertNotIn("차량 번호를 알려주세요", answer, text)
            self.assertIn("자리", answer, text)

    def test_finding_a_parked_car_still_asks_for_the_number(self):
        for i, text in enumerate(("내 차 어디 있어?", "차 어디에 세워뒀지?", "우리 차 어디 있는지 알려줘")):
            self.assertIn("차량 번호", self.e.handle(f"fc{i}", text=text)["text"], text)


class BackendWindowAfterAgentTests(unittest.TestCase):
    """대화 AI를 시도한 뒤 규칙 답이 서버를 조회할 시간이 남아 있어야 한다(11/11 실제 서버: '현재 정보를 확인하지 못했어요')."""

    def test_backend_deadline_is_renewed_after_the_agent(self):
        import time
        from unittest.mock import patch
        from chatbot import config, engine as e
        from chatbot.adapters import request_deadline
        now = time.monotonic()
        turn_token = e._turn_deadline.set(now + 8.0)            # 턴 전체 마감
        outer = request_deadline.set(now - 1.0)                 # 턴 시작 때 잡은 조회 마감(2초)이 이미 지남
        try:
            agent = request_deadline.set(now + 8.0)             # 대화 AI는 턴 마감까지 쓴다
            with patch.object(e, "ApiAdapter", type(e.data.inner)):
                e._restore_backend_deadline(agent)
            renewed = request_deadline.get()
            self.assertGreater(renewed, time.monotonic())                                   # 규칙 답이 조회할 수 있다
            self.assertLessEqual(renewed, time.monotonic() + config.BACKEND_TIMEOUT_SEC + 0.1)
            self.assertLessEqual(renewed, now + 8.0)                                        # 턴 마감은 넘지 않는다
        finally:
            request_deadline.reset(outer)
            e._turn_deadline.reset(turn_token)

    def test_mock_data_keeps_the_original_deadline(self):
        import time
        from chatbot import engine as e
        from chatbot.adapters import request_deadline
        outer = request_deadline.set(123.0)
        turn_token = e._turn_deadline.set(time.monotonic() + 8.0)
        try:
            e._restore_backend_deadline(request_deadline.set(999.0))
            self.assertEqual(request_deadline.get(), 123.0)     # 모의 데이터에서는 전과 같다
        finally:
            request_deadline.reset(outer)
            e._turn_deadline.reset(turn_token)
