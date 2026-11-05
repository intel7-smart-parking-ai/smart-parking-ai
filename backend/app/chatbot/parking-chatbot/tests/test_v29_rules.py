"""v2.9: 11/03 PM 결정(D-02~D-17) 반영 검사 — 차량 번호 10~99, 도면 칸 번호, 견적 결제, 가상 차량, 경보 보강, 기능 정리."""
from datetime import timedelta
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, parser, templates as T
from chatbot.adapters import CachedAdapter, MockAdapter, fee_details, iso, now
import test_api_contract as fixture

ROOT = Path(__file__).resolve().parent.parent


def ask(sid, text):
    r = e.handle(sid, text=text)
    if r.get("flow") == "confirm_vehicle":
        r = e.handle(sid, chip_id="confirm_yes", confirmation_token=r["confirmation_token"])
    return r


class ParserV29Tests(unittest.TestCase):
    def test_vehicle_number_table(self):
        cases = {"27번 차": "27", "27": "27", "십이번 차": "12", "구십구번 차": "99", "１７번": "17",
                 "7번 차": "short", "05번": "short", "00번": "short", "09번": "short", "오번 차": "short",
                 "100번": "invalid", "-12번": "invalid", "1.5번": "invalid", "12번 말고 15번": "15"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parser.parse_rules(text).params.get("car_id"), expected)

    def test_space_labels_and_counts_are_not_vehicles(self):
        for text in ("B-06 칸 비었어?", "개발원주차장 A-03", "B6 비었어?", "A 주차장 12번 칸 비었어?", "30분 무료야?", "50%"):
            with self.subTest(text=text):
                self.assertNotIn("car_id", parser.parse_rules(text).params)

    def test_lot_names_and_everyday_words(self):
        cases = {"개발원주차장 자리 있어?": "A", "부산역 주차장 빈자리": "B", "북항 자리 있어?": "C", "신세계 빈자리": "D",
                 "가 주차장": "A", "나 주차장 빈자리": "B", "라 주차장 C-13": "D", "B주차장": "B",
                 "나 주차했어": None, "어디 가?": None, "차 빼야 하는데 다 찼어?": None, "부산역 가는 길 알려줘": None}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parser.parse_rules(text).params.get("lot_id"), expected)
        self.assertEqual(parser.parse_rules("라 주차장 C-13").params.get("space_id"), "C-13")

    def test_virtual_vehicle_strings_keep_leading_zero(self):
        self.assertEqual(parser.parse_rules("1724번 차 어디 있어?").params["virtual_vehicle"], "1724")
        self.assertEqual(parser.parse_rules("17가1724 요금").params["virtual_vehicle"], "17가1724")
        self.assertEqual(parser.parse_rules("0123번 차").params["virtual_vehicle"], "0123")
        self.assertNotIn("car_id", parser.parse_rules("1724번 차").params)
        self.assertNotIn("virtual_vehicle", parser.parse_rules("2026년 요금").params)


class MockV29Tests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start()
        self.addCleanup(p.stop)
        e.reset_all()

    def test_short_and_invalid_numbers_do_not_start_flow(self):
        for text, expected in (("7번 차 어디 있어?", T.SHORT_VEHICLE), ("100번 차", T.INVALID_VEHICLE)):
            with self.subTest(text=text):
                r = e.handle(text, text=text)
                self.assertEqual(r["text"], expected)
                self.assertIsNone(e.get_session(text).pending)

    def test_location_uses_lot_name_and_drawing_number(self):
        self.assertIn("33번 차는 개발원주차장 B-01에 있어요", ask("s", "33번 차 어디 있어?")["text"])
        self.assertEqual(ask("t", "33번 차 어디 있어?")["card"]["data"]["space_id"], "B-01")

    def test_space_question_needs_lot_and_answers_state(self):
        r = ask("s", "B-06 비었어?")
        self.assertIn("어느 주차장의 B-06", r["text"])
        self.assertEqual([c["id"] for c in r["chips"]], ["lot_A", "lot_B", "lot_C", "lot_D"])
        r = ask("s", "개발원주차장 B-06 비었어?")
        self.assertIn("개발원주차장 B-06은 비어 있어요", r["text"])
        self.assertIn("장애인 전용", r["text"])
        self.assertIn("사용 중", ask("s", "개발원주차장 B-01 비었어?")["text"])

    def test_virtual_vehicle_is_pending_without_lookup(self):
        with patch.object(e.data, "find_car", side_effect=AssertionError("no lookup")):
            for text in ("1724번 차 어디 있어?", "17가1724 요금", "0123번 차"):
                with self.subTest(text=text):
                    self.assertEqual(ask(text, text)["text"], T.VIRTUAL_VEHICLE_PENDING)

    def test_exit_sentences_go_to_site_admin_without_llm_or_data(self):
        with patch("chatbot.parser.config.LLM_ENGINE", "langchain"), \
                patch("chatbot.llm_router.route", side_effect=AssertionError("no LLM")), \
                patch.object(e.data, "get_fee", side_effect=AssertionError("no data")):
            for text in ("차단기 안 열려요", "출구가 안 열려", "못 나가요"):
                with self.subTest(text=text):
                    r = e.handle(text, text=text)
                    self.assertEqual(r["meta"]["intent"], "exit_help")
                    self.assertIn("현장 관리자", r["text"])
                    self.assertIn("call_admin", [c["id"] for c in r["chips"]])
        not_found = ask("n", "35번 차 요금")
        self.assertEqual([c["id"] for c in not_found["chips"]][:2], ["change_vehicle", "call_admin"])

    def test_lot_rate_policies_match_billing_seed(self):
        expected = {"A": (1000, 500, 10000), "B": (2000, 700, 20000), "C": (3000, 800, 40000), "D": (4000, 1000, 50000)}
        for lot, (base, extra, cap) in expected.items():
            policy = config.catalog.rate_policy(lot)
            with self.subTest(lot=lot):
                self.assertEqual((policy["base_fee"], policy["extra_fee"], policy["daily_cap"]), (base, extra, cap))
                self.assertEqual(fee_details(0, policy), (base, False))
                self.assertEqual(fee_details(31, policy), (base + extra, False))
                units = -(-(cap - base) // extra)
                self.assertEqual(fee_details(30 + units * 10, policy), (cap, True))
        self.assertIn("부산역주차장: 처음 30분은 2,000원", ask("r", "부산역 요금 기준")["text"])

    def test_expired_or_foreign_quote_reconfirms_never_auto_pays(self):
        for change in ("expire", "foreign"):
            with self.subTest(change=change):
                e.reset_all()
                r = ask("q", "12번 차 결제할게")
                pending = e.get_session("q").pending
                if change == "expire":
                    pending.params["quote_expires_at"] = iso(now() - timedelta(seconds=10))
                else:
                    pending.params["quote_id"] = "someone-else"
                with patch.object(e.data.inner, "pay", wraps=e.data.inner.pay) as pay:
                    r = e.handle("q", chip_id="confirm_yes", confirmation_token=r["confirmation_token"])
                self.assertIn("요금이 갱신되었어요", r["text"])
                self.assertEqual(r["flow"], "confirm_action")
                self.assertFalse(e.data.get_fee("12")["paid"])
                self.assertLessEqual(pay.call_count, 1)

    def test_removed_features_leave_no_remnants(self):
        pattern = re.compile(r"check_exit_issue|after_check_exit_issue|\"exit_issue\"|'exit_issue'|STALE_POLICY|분 전 기준")
        for path in list((ROOT / "chatbot").glob("*.py")) + list((ROOT / "chatbot").glob("*.json")) + list((ROOT / "static").glob("*.html")):
            with self.subTest(path=path.name):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))

    def test_cache_fallback_still_marks_last_value(self):
        e.data.get_empty_spots()
        with patch.object(e.data.inner, "find_car", wraps=e.data.inner.find_car):
            ask("c", "33번 차 어디 있어?")
        with patch.object(e.data.inner, "find_car", side_effect=OSError("offline")):
            r = e.handle("c", chip_id="find_car")
        self.assertIn("마지막으로 받은 정보예요. 갱신 중", r["text"])


class ApiV29Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ApiContractTests("test_01_server_counts_and_uncertain")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.original = self.f.api._request_uncached

    def serve(self, lots=None, spaces=None, location=None, fee=None, alerts=None):
        def request(method, path, body=None):
            if lots is not None and path.endswith("/parking-lots"):
                return 200, {"items": lots, "as_of": iso(now())}, {}
            if spaces is not None and path.endswith("/spaces"):
                lot = path.split("/")[-2]
                return 200, {"items": spaces.get(lot, []), "as_of": iso(now())}, {}
            if location is not None and "/location" in path:
                return 200, {**location, "observed_at": iso(now())}, {}
            if fee is not None and "/sessions/" in path:
                return 200, fee, {}
            if alerts is not None and path.endswith("/alerts"):
                return 200, {"items": alerts, "as_of": iso(now())}, {}
            return self.original(method, path, body)
        return patch.object(self.f.api, "_request_uncached", side_effect=request)

    def test_drawing_labels_other_than_lot_letter_are_kept(self):
        labels = [f"A-{n:02d}" for n in range(1, 7)] + [f"B-{n:02d}" for n in range(1, 7)]
        lots = [{"lot_id": "A", "total_spaces": 12, "empty_spaces": 12, "uncertain_spaces": 0, "unavailable_spaces": 0}]
        spaces = {"A": [{"space_id": x, "status": "empty"} for x in labels]}
        location = {"car_id": "11", "lot_id": "A", "space_id": "B-06", "session_id": None, "status": "occupied"}
        with self.serve(lots, spaces, location):
            r = self.f.ask("충전 자리")
            zones = r["card"]["data"]["lots"]["A"]["zones"]
            self.assertEqual((zones["ev"]["total"], zones["accessible"]["total"], zones["general"]["total"]), (2, 1, 9))
            self.assertIn("11번 차는 개발원주차장 B-06에 있어요", self.f.ask("11번 차 어디 있어?", "loc")["text"])

    def test_rule_violating_server_car_marks_only_that_space(self):
        spaces = {k: [{"space_id": f"{k}-01", "status": "occupied", "car_id": "01"},
                      {"space_id": f"{k}-02", "status": "empty", "car_id": None},
                      {"space_id": f"{k}-03", "status": "uncertain"}, {"space_id": f"{k}-04", "status": "unavailable"}] for k in "ABC"}
        with self.serve(spaces=spaces):
            snapshot = self.f.adapter.get_empty_spots()
            self.assertEqual(snapshot["lots"]["A"]["empty"], 1)
            self.assertIn({"car_id": None, "lot_id": "A", "spot_no": 1, "space_id": "A-01", "session_id": None}, snapshot["unrecognized"])
            self.assertIn("1자리", self.f.ask("A 빈자리")["text"])

    def test_server_space_type_wins_and_vehicle_type_is_shown(self):
        spaces = {k: [{"space_id": f"{k}-01", "status": "empty", "space_type": "ev"},
                      {"space_id": f"{k}-02", "status": "occupied", "car_id": "11", "space_type": "general"},
                      {"space_id": f"{k}-03", "status": "uncertain"}, {"space_id": f"{k}-04", "status": "unavailable"}] for k in "ABC"}
        lots = [{"lot_id": k, "total_spaces": 4, "empty_spaces": 1, "uncertain_spaces": 1, "unavailable_spaces": 1} for k in "ABC"]
        with self.serve(lots, spaces):
            zones = self.f.adapter.get_empty_spots()["lots"]["A"]["zones"]
            self.assertEqual(zones["ev"], {"total": 1, "empty": 1, "uncertain": 0})
        location = {"car_id": "11", "lot_id": "A", "space_id": "A-02", "session_id": None, "status": "occupied", "vehicle_type": "ev"}
        with self.serve(location=location):
            self.assertIn("11번 차(전기차)는", self.f.ask("11번 차 어디 있어?", "vt")["text"])

    def fee(self, **extra):
        value = {"session_id": "session-01", "lot_id": "A", "space_id": "A-01", "car_id": "11", "amount_krw": 2500,
                 "duration_minutes": 70, "entry_at": iso(now()), "exit_at": None, "target_at": iso(now()),
                 "payment_status": "unpaid", "is_daily_capped": False, "quote_id": "quote-1",
                 "quote_expires_at": iso(now() + timedelta(minutes=5))}
        value.update(extra)
        return {k: v for k, v in value.items() if v is not ...}

    def test_missing_quote_never_posts(self):
        self.f.session_id = "session-01"
        with self.serve(fee=self.fee(quote_id=..., quote_expires_at=...)):
            r = self.f.ask("11번 결제")
        self.assertIn("요금 견적을 확인할 수 없어", r["text"])
        self.assertEqual(self.f.posts(), [])
        self.assertIsNone(e.get_session("s").pending)

    def test_quote_error_codes_reconfirm_and_conflicts_do_not_retry(self):
        for code, expected in (("QUOTE_EXPIRED", "요금이 갱신되었어요"), ("QUOTE_MISMATCH", "요금이 갱신되었어요"),
                               ("QUOTE_NOT_FOUND", "요금이 갱신되었어요"), ("PAYMENT_CONFLICT", "다른 결제가 처리 중"),
                               ("INVALID_PAYMENT_STATE", "이미 처리된 결제")):
            with self.subTest(code=code):
                e.reset_all()
                self.f.requests.clear()
                self.f.payment_code = code
                r = self.f.pay()
                self.assertIn(expected, r["text"])
                self.assertEqual(len(self.f.posts()), 1)

    def test_session_alias_values(self):
        self.f.session_id = "session-01"
        for state, closed in (("closed", True), ("exited", True), ("parked", False), ("active", False)):
            with self.subTest(state=state), self.serve(fee=self.fee(session_status=state)):
                self.f.adapter.cache.clear()
                self.assertEqual(self.f.api.get_fee("11")["session_status"], "closed" if closed else "active")

    def test_mock_event_source_and_violation_alerts(self):
        fire = {"alert_id": "f1", "lot_id": "A", "space_id": "A-05", "level": 1, "status": "active",
                "type": "mock_fire_led_observed", "occurred_at": iso(now()), "source": "manual"}
        violation = {"alert_id": "v1", "lot_id": "A", "space_id": "A-05", "status": "active",
                     "type": "restricted_space_violation", "space_type": "ev", "occurred_at": iso(now())}
        with self.serve(alerts=[fire]):
            self.assertIn("(모의 이벤트)", self.f.ask("경보 알려줘", "src")["text"])
        e.reset_all()
        with self.serve(alerts=[violation]):
            self.assertEqual(e.current_alert()["level"], 0)  # 위반은 화재 레벨과 섞지 않는다
            self.assertIn("개발원주차장 A-05 전기차 전용 칸에 일반 차량", self.f.ask("위반 차량 있어?", "v1")["text"])
        e.reset_all()
        with self.serve(alerts=[]):
            self.assertIn("확인된 위반 차량은 없어요", self.f.ask("불법 주차한 차 있어?", "v2")["text"])
        e.reset_all()
        self.assertIn("확인할 수 없어요", self.f.ask("위반 차량 있어?", "v3")["text"])


if __name__ == "__main__":
    unittest.main()
