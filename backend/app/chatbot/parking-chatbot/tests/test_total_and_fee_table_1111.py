"""전체 빈자리 합계와 요금표 (11/11 시연에서 나온 두 가지).

1) 합계: 대화 AI가 주차장별 숫자를 직접 더해 "총 91자리"라고 썼다. 근거 검사는 조회 결과에 없는 숫자를 막기 때문에
   맞게 더한 답도 버려지고(3~5초 지연), 틀리게 더한 값이 조회 결과의 다른 숫자(칸 수 64 등)와 우연히 같으면 통과할 수 있었다.
   → 합계는 코드가 계산해 조회 결과(summary.total_empty)에 싣고, 답의 합계는 그 값과 같아야 한다.
   또 "21개 (카메라 확인 중인 칸 1개 포함)"처럼 확인 중인 칸을 빈자리에 포함된 것으로 말한 답이 나갔다 → 막는다.
2) 요금표: 화면의 '주차요금' 버튼("주차요금 안내해줘")이 내 차 요금으로 분류돼 차량 번호를 물었다.
   → 요금표(주차장마다 한 줄)를 규칙이 바로 낸다. 내 차 요금은 '요금 얼마야', '23번 요금'처럼 따로 묻는다.
"""
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, navigation, parser, response_locale as locale
from chatbot.agents.evidence import REPAIR_HINTS, check_answer
from chatbot.tools.read import ReadTools


def seen(tool, result):
    return [{"tool": tool, "call_id": "1", "result": result}]


class TotalTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        self.tools = ReadTools(e.data, e.current_alert)

    def rule_total(self):
        return navigation.overview(e.data.get_empty_spots(), e.current_alert())[1]

    def test_tool_results_carry_the_code_computed_total(self):
        total = self.rule_total()
        self.assertGreater(total, 0)
        for tool, result in (("parking_status", self.tools.parking_status(None)), ("compare_lots", self.tools.compare_lots(None))):
            summary = result["data"]["summary"]
            self.assertEqual(summary["total_empty"], total, tool)
            self.assertEqual(summary["total_empty"], sum(row["empty"] for row in result["data"]["rows"]), tool)
            self.assertIn("직접 더하지 않는다", summary["total_empty_rule"])

    def test_single_lot_has_no_total(self):
        self.assertNotIn("summary", self.tools.parking_status(["C"])["data"])

    def test_total_leaves_out_lots_the_rule_answer_leaves_out(self):
        e.data.set_alert(2, "A", 3)                              # 경보 중인 개발원은 합계에서 뺀다(규칙 답과 같은 기준)
        result = self.tools.compare_lots(None)
        self.assertEqual(result["data"]["summary"]["total_empty"], self.rule_total())
        self.assertEqual(result["data"]["summary"]["total_excludes"], ["개발원주차장"])
        self.assertLess(result["data"]["summary"]["total_empty"], sum(row["empty"] for row in result["data"]["rows"]))

    def test_zone_total(self):
        result = self.tools.parking_status(None, "ev")
        self.assertEqual(result["data"]["summary"]["total_empty"],
                         navigation.overview(e.data.get_empty_spots(), e.current_alert(), "ev")[1])

    # ---- 근거 검사
    def test_the_code_total_passes_and_any_other_total_is_blocked(self):
        result = self.tools.compare_lots(None)
        total = result["data"]["summary"]["total_empty"]
        observed = seen("compare_lots", result)
        rows = {row["lot_id"]: row for row in result["data"]["rows"]}
        listing = "\n".join(f"· {row['name']}: {row['empty']}자리" for row in rows.values() if row["empty"])
        self.assertIsNone(check_answer(f"현재 모든 주차장의 빈자리를 합치면 총 {total}자리입니다.\n\n{listing}", "전체 빈자리 몇 개야?", observed))
        self.assertIsNone(check_answer(f"전체 주차장 빈자리는 총 {total}대예요.", "전체 주차 가능 대수 알려줘", observed))
        # 조회 결과에 있는 다른 숫자(김해공항 전체 칸 수)와 우연히 같은 합계는 전에는 통과했다
        capacity = rows["C"]["total"]
        self.assertNotEqual(capacity, total)
        self.assertEqual(check_answer(f"전체 주차장 빈자리는 총 {capacity}자리예요.", "전체 빈자리 몇 개야?", observed), "wrong_total")
        self.assertEqual(check_answer(f"모두 합쳐서 {rows['C']['empty']}대 남았어요.", "합쳐서 몇 대야?", observed), "wrong_total")
        # 조회 결과에 아예 없는 숫자는 전과 같이 막힌다
        self.assertEqual(check_answer(f"전체 주차장 빈자리는 총 {total + 1000}자리예요.", "전체 빈자리 몇 개야?", observed), "unsupported_number")
        self.assertIn("total_empty", REPAIR_HINTS["wrong_total"])

    def test_one_lots_own_capacity_is_not_a_total_claim(self):
        result = self.tools.compare_lots(None)
        row = next(r for r in result["data"]["rows"] if r["lot_id"] == "C")
        answer = f"김해공항 주차장은 총 {row['total']}칸 중 {row['empty']}칸이 비어 있어요."
        self.assertIsNone(check_answer(answer, "김해공항 자리 있어?", seen("compare_lots", result)))

    def test_total_said_as_jeonche_after_a_per_lot_list(self):
        # 11/11 내부 검사: "나 전기차야" → "· 부산역주차장: 2자리\n\n전체 12자리의 전기차 자리가 있어요"가
        # 합계로 잡히지 않고 바로 앞 주차장(부산역)의 값과 비교돼 버려졌다(mismatched_lot_value)
        result = self.tools.compare_lots(None, "ev")
        total = result["data"]["summary"]["total_empty"]
        listing = "\n".join(f"· {row['name']}: {row['empty']}자리" for row in result["data"]["rows"] if row["empty"])
        observed = seen("compare_lots", result)
        for tail in (f"전체 {total}자리의 전기차 자리가 있어요.", f"모두 {total}곳이 비어 있어요.", f"전체 {total}자리가 비어 있어요."):
            self.assertIsNone(check_answer(f"전기차 충전 구역의 빈자리예요.\n\n{listing}\n\n{tail}", "나 전기차야", observed), tail)
        self.assertIsNotNone(check_answer(f"전기차 충전 구역의 빈자리예요.\n\n{listing}\n\n전체 {total + 1}자리가 비어 있어요.",
                                          "나 전기차야", observed))
        lots = result["data"]["summary"]["lot_count"]
        self.assertIsNone(check_answer(f"총 {lots}곳의 주차장을 확인했어요.\n\n{listing}", "나 전기차야", observed))   # 주차장 수는 합계가 아니다

    def test_total_after_the_per_lot_list_on_one_line(self):
        result = self.tools.compare_lots(None)
        total = result["data"]["summary"]["total_empty"]
        rows = [row for row in result["data"]["rows"] if row["empty"]]
        answer = ", ".join(f"{row['name']} {row['empty']}자리" for row in rows) + f"로 총 {total}자리예요."
        self.assertIsNone(check_answer(answer, "전체 빈자리 몇 개야?", seen("compare_lots", result)))
        wrong = ", ".join(f"{row['name']} {row['empty']}자리" for row in rows) + f"로 총 {total + 1}자리예요."
        self.assertIsNotNone(check_answer(wrong, "전체 빈자리 몇 개야?", seen("compare_lots", result)))

    # ---- 카메라 확인 중인 칸은 빈자리 수에 들어 있지 않다
    def test_uncertain_spaces_are_not_part_of_the_vacancy_count(self):
        e.data.set_uncertain("C", next(n for n in range(1, 65) if self._free("C", n)), True)
        result = self.tools.parking_status(None)
        row = next(r for r in result["data"]["rows"] if r["lot_id"] == "C")
        self.assertEqual(row["uncertain"], 1)
        self.assertGreater(row["empty"], 0)
        self.assertIn(f"빈자리 {row['empty']}칸에 들어 있지 않다", row["camera_check"])
        self.assertNotIn("확인된 빈자리는 없고", row["camera_check"])          # 빈자리가 있는 주차장에 쓰면 안 되는 문장
        observed = seen("parking_status", result)
        for answer in (f"김해공항 주차장: {row['empty']}개 (카메라 확인 중인 칸 1개 포함)",
                       f"김해공항 주차장은 {row['empty']}자리예요. 확인 필요한 1칸을 포함한 숫자예요."):
            self.assertEqual(check_answer(answer, "김해공항 자리 있어?", observed), "uncertain_included", answer)
        for answer in (f"김해공항 주차장은 {row['empty']}자리예요. 그 밖에 1칸은 카메라 확인 중이에요.",
                       f"김해공항 주차장은 {row['empty']}자리예요. 카메라 확인 중인 1칸은 포함되지 않은 숫자예요."):
            self.assertIsNone(check_answer(answer, "김해공항 자리 있어?", observed), answer)

    @staticmethod
    def _free(lot, number):
        try:
            e.data.set_uncertain(lot, number, True)
            e.data.set_uncertain(lot, number, False)
            return True
        except ValueError:
            return False

    def test_lot_with_no_confirmed_vacancy_keeps_its_wording(self):
        for number in range(1, e.data.inner.capacity["A"] + 1):
            try:
                e.data.set_uncertain("A", number, True)
            except ValueError:
                pass
        row = self.tools.parking_status(["A"])["data"]["rows"][0]
        self.assertEqual(row["empty"], 0)
        self.assertIn("'만차'라고 하지 않는다", row["camera_check"])

    # ---- 대화 AI: 합계를 틀리게 쓰면 고쳐 쓰고, 맞게 쓰면 그대로 나간다
    def test_ai_answer_with_the_code_total_goes_out(self):
        try:
            from langchain_core.messages import AIMessage
        except ImportError:
            self.skipTest("optional LangChain not installed")
        from chatbot import agents
        from test_agent_runtime_v3 import ScriptedModel
        total = self.rule_total()
        run = agents.run_conversation
        answer = lambda messages: AIMessage(content=f"전체 주차장 빈자리는 총 {total}자리예요.")   # noqa: E731
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(agents, "run_conversation",
                             side_effect=lambda *a, **k: run(*a, model=ScriptedModel([answer] * 4), **k)):
            r = e.handle("t1", text="모든 주차장 합쳐서 몇 자리 남았어?")
        self.assertIn(f"총 {total}자리", r["text"])
        self.assertIn("agent:answer", r["meta"]["route"])


class FeeTableTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        locale.load_seed()
        self.addCleanup(e.reset_all)

    def test_fee_guide_sentences_are_fee_table_questions(self):
        for text in ("주차요금 안내해줘", "주차 요금 안내", "요금 안내해줘", "요금표 보여줘", "주차요금 알려줘", "요금 정책이 어떻게 돼?",
                     "주차비 안내 좀 해줘"):
            r = parser.parse_rules(text)
            self.assertEqual((r.intent, r.params.get("fee_table")), ("fee_rule", True), text)

    def test_own_fee_and_lot_fee_questions_are_not(self):
        for text, intent in (("요금 얼마야?", "get_fee"), ("주차비 알려줘", "get_fee"), ("23번 요금 안내해줘", "get_fee"),
                             ("내 차 요금 안내해줘", "get_fee"), ("3시간 요금 안내해줘", "get_fee")):
            r = parser.parse_rules(text)
            self.assertEqual(r.intent, intent, text)
            self.assertFalse(r.params.get("fee_table"), text)
        r = parser.parse_rules("부산역 요금 안내해줘")                 # 주차장을 말하면 그 주차장의 요금
        self.assertFalse(r.params.get("fee_table"))

    def test_the_fee_button_shows_every_lots_fee(self):
        r = e.handle("f1", text="주차요금 안내해줘")
        lines = r["text"].split("\n")
        self.assertEqual(lines[0], "주차장별 요금표예요.")
        self.assertEqual(lines[1], "· 개발원주차장: 처음 30분은 1,000원, 이후 10분마다 500원, 하루 최대 10,000원")
        self.assertEqual(len(lines), 2 + len(config.catalog.ids()))
        for name in ("부산역주차장", "김해공항 주차장", "신세계백화점주차장"):
            self.assertIn(f"· {name}: 처음", r["text"])
        self.assertNotIn("차량 번호를 알려주세요", r["text"])
        self.assertIn("rule:fee_rule", r["meta"]["route"])

    def test_the_fee_table_skips_the_model(self):
        from chatbot import agents
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=AssertionError("모델을 부르면 안 된다")):
            r = e.handle("f2", text="주차요금 안내해줘")
        self.assertIn("agent:skip", r["meta"]["route"])
        self.assertIn("주차장별 요금표예요.", r["text"])

    def test_own_fee_still_asks_for_the_car(self):
        r = e.handle("f3", text="요금 얼마야?")
        self.assertIn("차량 번호를 알려주세요", r["text"])
        r = e.handle("f4", text="23번 요금 얼마야?")
        self.assertIn("원", r["text"])
        self.assertNotIn("요금표", r["text"])

    def test_lot_fee_question_is_unchanged(self):
        r = e.handle("f5", text="부산역 요금 안내해줘")
        self.assertIn("부산역주차장: 처음 30분은 2,000원", r["text"])
        self.assertNotIn("개발원주차장", r["text"])

    def test_fee_table_is_translated_without_the_model(self):
        text = e.handle("f6", text="주차요금 안내해줘")["text"]
        with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
            en, zh, ja = (locale._translate_known((text,), language)[0] for language in ("en", "zh-CN", "ja"))
        self.assertIn("· Busan Station Parking: KRW 2,000 for the first 30 minutes, then KRW 700 every 10 minutes, up to KRW 20,000 per day", en)
        self.assertIn("· 釜山站停车场：前30分钟2,000韩元，之后每10分钟700韩元，每日最高20,000韩元", zh)
        self.assertIn("· 釜山駅駐車場：最初の30分は2,000ウォン、以降10分ごとに700ウォン、1日最大20,000ウォン", ja)


if __name__ == "__main__":
    unittest.main()
