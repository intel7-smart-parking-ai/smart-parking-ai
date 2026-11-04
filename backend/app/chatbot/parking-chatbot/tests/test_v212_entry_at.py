"""Wire tests for anonymous candidates and the shared briefing snapshot."""
from datetime import timedelta
from collections import Counter
import unittest

from chatbot import engine
from chatbot.adapters import iso, now
from tests import test_api_contract as wire


class EntryTimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = wire.ApiContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.alerts = []
        self.fixture.mock_header = False
        self.anonymous = {}
        self.failed_lot = None
        self.entry_at = iso(now().replace(hour=14, minute=20, second=0, microsecond=0) - timedelta(days=1))
        handler = self.fixture.server.RequestHandlerClass
        reply = handler.reply

        def altered(instance, body, status=200):
            if instance.path.endswith("/spaces"):
                lot = instance.path.split("/")[-2]
                if lot == self.failed_lot:
                    return reply(instance, {}, 503)
                for index, row in enumerate(body["items"]):
                    row["car_id"] = None
                    row["status"] = "empty"
                    if lot == "A" and index in self.anonymous:
                        row.update(status="uncertain" if index == 2 else "occupied", entry_at=self.anonymous[index])
                    if lot == "A" and self.fixture.alerts and index == 0:
                        row.update(status="occupied", car_id="10", vehicle_type="ev")
            return reply(instance, body, status)

        handler.reply = altered

    def ask(self, sid="candidate"):
        return self.fixture.ask("27번 차 어디 있어?", sid)

    def assert_no_candidate(self, response):
        self.assertIsNone(response["card"])
        self.assertIn("27번 차의 입차 기록이 없어요.", response["text"])
        self.assertIn("unrecognized", [chip["id"] for chip in response["chips"]])

    def test_01_missing_entry_time(self):
        self.anonymous = {0: None}
        self.assert_no_candidate(self.ask())

    def test_02_one_candidate(self):
        self.anonymous = {0: self.entry_at}
        response = self.ask()
        self.assertIn("개발원주차장 A-01에 있어요 (14:20 입차)", response["text"])
        self.assertEqual(response["card"]["type"], "unrecognized_candidates")
        self.assertTrue(any(chip["id"].startswith("candidate:") for chip in response["chips"]))

    def test_03_incomplete_candidate_set(self):
        self.anonymous = {0: self.entry_at, 2: None}
        self.assert_no_candidate(self.ask())

    def test_04_invalid_entry_times(self):
        for index, invalid in enumerate((iso(now() + timedelta(seconds=30)), "2026-11-04T14:20:00", 7, "invalid")):
            with self.subTest(invalid=invalid):
                self.anonymous = {0: self.entry_at, 2: invalid}
                self.assert_no_candidate(self.ask(str(index)))

    def test_05_four_candidates(self):
        self.anonymous = dict.fromkeys(range(4), self.entry_at)
        self.assertIn("번호 미인식 차량이 4대 있어요. 관리자에게 확인해 주세요.", self.ask()["text"])

    def choose(self, response):
        chip = next(chip["id"] for chip in response["chips"] if chip["id"].startswith("candidate:"))
        return engine.handle("candidate", chip_id=chip)

    def test_06_revalidation_checks_same_entry_time(self):
        self.anonymous = {0: self.entry_at}
        response = self.ask()
        self.anonymous = {0: iso(now() - timedelta(minutes=2))}
        self.assertIn("후보 차량 정보가 바뀌었어요", self.choose(response)["text"])

    def test_07_candidates_cannot_pay(self):
        self.anonymous = {0: self.entry_at}
        self.assertIn("후보 위치예요", self.choose(self.ask())["text"])
        for chip in ("fee", "pay"):
            self.assertIn("관리자 확인 후 가능해요", engine.handle("candidate", chip_id=chip)["text"])
        self.assertEqual(self.fixture.posts(), [])

    def set_alert(self):
        self.fixture.alerts = [{"alert_id": "v212", "type": "mock_fire_led", "status": "active",
                                "level": 2, "lot_id": "A", "space_id": "A-02", "occurred_at": iso(now())}]

    def test_08_lv2_never_offers_candidates(self):
        self.anonymous = {2: self.entry_at}
        self.set_alert()
        result = self.ask()
        self.assertIsNotNone(result["safety"])
        self.assertNotEqual((result["card"] or {}).get("type"), "unrecognized_candidates")

    def test_09_briefing_without_entry_time_and_on_space_failure(self):
        self.set_alert()
        for entry_at in (None, self.entry_at):
            self.anonymous = {2: entry_at}
            engine.reset_all()
            engine.current_alert()
            briefing = engine.incidents.active()["briefing"]
            self.assertIn("A-01: 10번 · 전기차", briefing["adjacent"])
            self.assertEqual(briefing["ev_count"], 1)
        engine.reset_all()
        self.failed_lot = "A"
        engine.current_alert()
        self.assertIn("A-01: 확인 불가", engine.incidents.active()["briefing"]["adjacent"])

    def test_10_no_duplicate_spaces_per_chat(self):
        self.set_alert()
        engine.handle("empty", chip_id="empty_all")
        calls = Counter(path for method, path, _ in self.fixture.requests if path.endswith("/spaces"))
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(count == 1 for count in calls.values()), calls)

    def test_pm_rate_path_and_row_provenance(self):
        handler = self.fixture.server.RequestHandlerClass
        get = handler.do_GET

        def pm(instance):
            if instance.path == "/api/v1/rate-policies":
                return instance.reply({"items": [{"lot_id": "A", "base_minutes": 30, "base_fee": 1000,
                                                   "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}]})
            return get(instance)

        handler.do_GET = pm
        reply = handler.reply
        source = {"value": "vision"}

        def tagged(instance, body, status=200):
            if instance.path.endswith("/spaces"):
                body["items"][0]["source"] = source["value"]
            if instance.path == "/api/v1/vehicles/11/location":
                body["source"] = source["value"]
            return reply(instance, body, status)

        handler.reply = tagged
        self.assertIn("1,000원", self.fixture.ask("요금표 알려줘")["text"])
        for value, expected in (("vision", False), ("manual", True), ("simulation", True)):
            source["value"] = value
            self.assertEqual("※ 시연용 데이터예요" in self.fixture.ask("A 빈자리", value)["text"], expected)
            self.assertEqual("※ 시연용 데이터예요" in self.fixture.ask("11번 차 위치", value)["text"], expected)
