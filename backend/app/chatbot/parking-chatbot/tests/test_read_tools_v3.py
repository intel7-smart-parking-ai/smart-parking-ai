import asyncio
import importlib.util
import unittest
from unittest.mock import Mock

from chatbot.adapters import CachedAdapter, DataUnavailable, MockAdapter
from chatbot.dialog import interpret, render_query
from chatbot.state import Session
from chatbot.tools import ReadTools


class ReadToolsTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CachedAdapter(MockAdapter())
        self.tools = ReadTools(self.adapter, lambda: {"level": 0}, allowed_car_id="23", allowed_lots=["C", "D"])

    def test_candidate_scope_and_zone_are_preserved(self):
        result = self.tools.parking_status(["C", "D"], "ev")
        self.assertTrue(result["ok"])
        self.assertEqual(["C", "D"], [row["lot_id"] for row in result["data"]["rows"]])
        self.assertFalse(self.tools.parking_status(["A"], "ev")["ok"])
        self.assertFalse(self.tools.vehicle_info("30")["ok"])
        self.assertFalse(self.tools.propose_action("pay_fee", {"car_id": "30"})["ok"])
        self.assertTrue(self.tools.propose_action("pay_fee", {"car_id": "23"})["ok"])

    def test_zone_correction_never_excludes_venue(self):
        q = interpret("공항에서 일반 자리 말고 충전 자리", Session())
        self.assertEqual((q.zone, q.candidates), ("ev", ("C",)))
        rd = render_query(q, self.tools)
        self.assertIn("김해공항", rd.text)
        self.assertNotIn("신세계", rd.text)

    def test_comparison_excludes_full_low_rate(self):
        q = interpret("공항이랑 신세계 중 충전 자리 있고 기본요금 싼 곳", Session())
        snapshot = self.adapter.get_empty_spots()
        snapshot["lots"]["C"]["zones"]["ev"]["empty"] = 0
        tools = ReadTools(self.adapter, lambda: {"level": 0}, occupancy_provider=lambda: snapshot)
        reply = render_query(q, tools)
        self.assertIn("만차", reply.text)
        self.assertIn("가장 낮은 곳은 신세계", reply.text)
        self.assertNotIn("개발원", reply.text)

    def test_refuse_mismatched_visit_amount_but_keep_location(self):
        adapter = Mock()
        adapter.find_car.return_value = {"car_id": "23", "lot_id": "A", "session_id": "old", "space_id": "A-03"}
        adapter.get_fee.return_value = {"car_id": "23", "lot_id": "A", "session_id": "new", "fee": 7100}
        tools = ReadTools(adapter, lambda: {"level": 0}, allowed_car_id="23")
        result = render_query(interpret("23번 차 위치랑 요금", Session()), tools)
        self.assertIn("A-03", result.text)
        self.assertNotIn("7,100", result.text)
        self.assertTrue(result.meta["partial"])
        adapter.get_fee.side_effect = DataUnavailable("offline")
        self.assertTrue(tools.vehicle_info("23")["data"]["partial"])

    @unittest.skipUnless(importlib.util.find_spec("langchain_core"), "optional LangChain")
    def test_structured_tools_accept_async_typed_arguments(self):
        tools = {t.name: t for t in self.tools.langchain_tools()}
        # v3.1: 계산·요약 조회 도구 5개 추가(모두 읽기 전용). 기존 5개 이름은 유지한다.
        self.assertEqual(set(tools), {"parking_status", "vehicle_info", "propose_action", "fee_policies", "search_knowledge",
                                      "compare_lots", "estimate_fee", "alert_summary", "violations", "call_status"})
        result = asyncio.run(tools["parking_status"].ainvoke({"lot_ids": ["D"], "zone": "ev"}))
        self.assertEqual(result["source"]["scope"], ["D"])
        self.assertTrue(result["ok"])
