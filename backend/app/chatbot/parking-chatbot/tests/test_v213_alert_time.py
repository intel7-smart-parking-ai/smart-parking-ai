"""v2.13: /alerts 시각 필드명이 미결(detected_at/occurred_at)이라 둘 다 받는지 검사. 실제 서버 없음."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from chatbot import templates
from chatbot.adapters import ApiAdapter, _alert_time, iso, now


def alert(**time_fields):
    return {"alert_id": "a1", "lot_id": "A", "space_id": "A-01", "level": 2, "status": "active",
            "type": "mock_fire_led", **time_fields}


class AlertTimeFieldTests(unittest.TestCase):
    def read(self, item):
        api = ApiAdapter("http://127.0.0.1:9")
        with patch.object(api, "_request", return_value=(200, {"items": [item], "as_of": iso(now())}, {})):
            return api.get_alert()

    def test_helper_prefers_occurred_at_then_detected_at(self):
        self.assertEqual(_alert_time({"occurred_at": "o", "detected_at": "d"}), "o")
        self.assertEqual(_alert_time({"detected_at": "d"}), "d")
        self.assertEqual(_alert_time({"occurred_at": "", "detected_at": "d"}), "d")
        self.assertIsNone(_alert_time({"occurred_at": 3}))
        self.assertIsNone(_alert_time({}))

    def test_detected_at_alone_shows_alert_time(self):
        started = iso(now() - timedelta(minutes=2))
        result = self.read(alert(detected_at=started))
        self.assertEqual(result["level"], 2)
        self.assertEqual(result["started_at"], started)
        self.assertRegex(templates.api_alert(result), r"\(\d\d:\d\d\)")

    def test_occurred_at_behaviour_is_unchanged(self):
        started = iso(now() - timedelta(minutes=1))
        self.assertEqual(self.read(alert(occurred_at=started))["started_at"], started)

    def test_missing_time_still_reports_alert_without_time(self):
        result = self.read(alert())
        self.assertEqual(result["level"], 2)
        self.assertIsNone(result["started_at"])
        self.assertNotRegex(templates.api_alert(result), r"\(\d\d:\d\d\)")


if __name__ == "__main__":
    unittest.main()
