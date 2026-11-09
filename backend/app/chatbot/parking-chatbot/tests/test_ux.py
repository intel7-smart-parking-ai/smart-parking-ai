from unittest.mock import patch as _patch
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from chatbot import engine as e
from chatbot.adapters import CachedAdapter, MockAdapter, DataUnavailable
from chatbot.state import Conflict


class UXTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop)
        e.reset_all()

    def test_context_recovers_after_exit_and_failure(self):
        r = e.handle("s", text="33번 차 위치")
        self.assertEqual(r["vehicle_context"]["spot_no"], 7)
        e.data.exit_car("33")
        self.assertEqual(e.updates("s")["vehicle_context"]["state"], "not_found")
        with patch.object(e.data, "find_car", side_effect=DataUnavailable()):
            self.assertEqual(e.updates("s")["vehicle_context"]["state"], "unavailable")

    def test_position_card_and_context_share_one_read(self):
        with patch.object(e.data, "find_car", wraps=e.data.find_car) as read:
            r = e.handle("s", text="33번 차 위치")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(r["card"]["data"]["spot_no"], r["vehicle_context"]["spot_no"])

    def test_change_vehicle_cancels_payment(self):
        e.handle("s", text="33번 결제"); e.handle("s", text="네")
        r = e.handle("s", chip_id="change_vehicle")
        self.assertIsNone(r["vehicle_context"]["car_id"])
        e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_immediate_call_during_every_alert_level(self):
        for level in (0, 1, 2):
            e.data.set_alert(level, "A", 6)
            r = e.handle(str(level), chip_id="call_admin")
            self.assertEqual(r["card"]["type"], "admin_call")
            self.assertEqual(r["flow"], "idle")
        self.assertEqual(len(e.admin_calls()), 3)

    def test_repeat_click_different_ids_deduplicates_open_call(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            r = list(pool.map(lambda i: e.handle("s", chip_id="call_admin", request_id=str(i)), range(20)))
        self.assertEqual(len(e.admin_calls()), 1)
        self.assertTrue(all(x["card"]["data"]["id"] == 1 for x in r))






    def test_call_does_not_depend_on_backend_success(self):
        e.get_session("s").car_id = "33"
        with patch.object(e.data, "find_car", side_effect=DataUnavailable()):
            r = e.handle("s", chip_id="call_admin")
        self.assertEqual(r["card"]["type"], "admin_call")
        self.assertIn("미확인", r["card"]["data"]["summary"])
