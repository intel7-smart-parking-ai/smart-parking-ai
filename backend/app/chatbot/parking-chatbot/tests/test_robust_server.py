"""B/C: real HTTP fault injection and bounded response checks."""
from copy import deepcopy
import socket
import time
import unittest
from unittest.mock import patch
from chatbot import config, engine as e
from chatbot.adapters import ApiAdapter, CachedAdapter
from robust_support import FaultServer
from safety_check import assert_safe


class RobustServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = FaultServer()

    @classmethod
    def tearDownClass(cls):
        cls.http.close()

    def setUp(self):
        self.settings = deepcopy(config.catalog.settings)
        self.http.reset()
        self.patches = [patch.object(config, "DATA_SOURCE", "api"), patch.object(config, "ALERT_SHARE_SEC", 0),
                        patch.object(config, "BACKEND_TIMEOUT_SEC", .15), patch.object(config, "REQUEST_BUDGET_SEC", .4),
                        patch.object(e, "data", CachedAdapter(ApiAdapter(self.http.base))),
                        patch("chatbot.adapters.calc_fee", side_effect=AssertionError("API must not calculate fees"))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        config.catalog.configure(self.settings)

    def probe(self, target):
        e.reset_all()
        e.get_session("probe").car_id = "11"
        if target == "payment":
            quote = e.handle("probe", text="결제")
            result = e.handle("probe", chip_id="confirm_yes", confirmation_token=quote["confirmation_token"])
        else:
            text = {"lots": "A 빈자리", "spaces": "A 전기차 빈자리", "location": "내 차 위치",
                    "fee": "요금", "alerts": "경보"}[target]
            result = e.handle("probe", text=text)
        assert_safe(result, {"api": True, "hidden": ["untrusted-server-message"]})
        return result

    def test_status_body_matrix_all_six_endpoints(self):
        cases = [(code, {"error": {"message": "untrusted-server-message"}}) for code in (500, 502, 503, 404)]
        cases += [(200, body) for body in (b"<html>untrusted-server-message</html>", b'{"items":[', b"", {})]
        for target in ("lots", "spaces", "location", "fee", "payment", "alerts"):
            for status, body in cases:
                with self.subTest(target=target, status=status, body=str(body)[:30]):
                    self.http.target, self.http.status, self.http.value = target, status, body
                    answer = self.probe(target)
                    if target == "spaces":
                        data = (answer.get("card") or {}).get("data", {}).get("lots", {}).get("A", {})
                        self.assertNotIn("zones", data)
                    else:
                        self.assertIn("확인", answer["text"])

    def test_unhashable_status_and_malformed_alert_items_never_escape(self):
        for target, field in (("spaces", "status"), ("location", "status"), ("fee", "payment_status"), ("alerts", "status")):
            for invalid in ([], {}):
                with self.subTest(target=target, invalid=invalid):
                    body = self.http.normal(target)
                    if target == "spaces":
                        body["items"][0][field] = invalid
                    elif target == "alerts":
                        body["items"] = [invalid]
                    else:
                        body[field] = invalid
                    self.http.target, self.http.value = target, body
                    self.probe(target)

    def test_location_unknown_state_and_missing_time_not_certainty(self):
        for patch_value in ({"status": "parked"}, {"observed_at": None}, {"confidence": "0.9"}):
            with self.subTest(value=patch_value):
                self.http.target = "location"
                self.http.value = {**self.http.normal("location"), **patch_value}
                answer = self.probe("location")
                self.assertIsNone(answer["card"])
                self.assertNotIn("1번 칸에 있어요", answer["text"])

    def test_duplicate_lots_and_incomplete_spaces_not_invented(self):
        self.http.target = "lots"
        self.http.value = self.http.normal("lots")
        self.http.value["items"] *= 2
        with self.subTest(kind="duplicate lots"):
            self.assertIsNone(self.probe("lots")["card"])
        self.http.target = "spaces"
        for rows in ([], [{"space_id": "bad", "status": "empty"}], [{"space_id": "A-02", "status": "empty"}]*4):
            with self.subTest(rows=rows):
                self.http.value = {"items": rows}
                answer = self.probe("spaces")
                self.assertNotIn("zones", answer["card"]["data"]["lots"]["A"])

    def test_numeric_and_flag_types_rejected(self):
        for target, field, values in (("lots", "empty_spaces", (-1, 5, "1")),
                                      ("fee", "amount_krw", (-500, "2500", True)),
                                      ("fee", "is_daily_capped", ("true", 1)),
                                      ("payment", "amount_krw", (-500, "2500", True))):
            for invalid in values:
                with self.subTest(target=target, field=field, invalid=invalid):
                    self.http.target, self.http.value = target, self.http.normal(target)
                    item = self.http.value["items"][0] if target == "lots" else self.http.value
                    item[field] = invalid
                    answer = self.probe(target)
                    self.assertIsNone(answer["card"])
                    self.assertNotIn("하루 최대 요금이 적용", answer["text"])

    def test_timeout_headers_slow_body_and_closed_port(self):
        for mode in ("headers", "body"):
            with self.subTest(mode=mode):
                server = FaultServer()
                try:
                    server.target, server.mode = "lots", mode
                    e.data = CachedAdapter(ApiAdapter(server.base))
                    started = time.monotonic()
                    answer = self.probe("lots")
                    self.assertLess(time.monotonic()-started, 1.4)
                    self.assertIn("확인", answer["text"])
                finally:
                    server.close()
        e.data = CachedAdapter(ApiAdapter("http://127.0.0.1:1"))
        self.assertIn("확인", self.probe("lots")["text"])

    def test_dns_failure_and_case_insensitive_mock_header(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("injected DNS failure")):
            self.assertIn("확인", self.probe("lots")["text"])
        self.http.header = "x-parkflow-data-source"
        e.data = CachedAdapter(ApiAdapter(self.http.base))
        self.assertEqual(self.probe("lots")["text"].count("※ 시연용 데이터예요"), 1)

    def test_ten_megabytes_and_ten_thousand_items_bounded(self):
        for body in (b" "*10_000_000, {"items": self.http.normal("lots")["items"]*10_000, "as_of": self.http.at}):
            self.http.target, self.http.value = "lots", body
            began = time.monotonic()
            answer = self.probe("lots")
            self.assertLess(time.monotonic()-began, 1.4)
            self.assertLess(len(answer["text"]), 1000)

    def test_success_outage_recovery_not_failure_cached(self):
        e.reset_all()
        e.get_session("probe").car_id = "11"
        self.assertIn("2,500", e.handle("probe", text="요금")["text"])
        self.http.target, self.http.status = "fee", 503
        self.assertIn("확인", e.handle("probe", text="요금")["text"])
        self.http.target = None
        self.assertIn("2,500", e.handle("probe", text="요금")["text"])
