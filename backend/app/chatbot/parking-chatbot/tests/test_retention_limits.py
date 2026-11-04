"""Record limits preserve unresolved work and release report locks before reads."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from chatbot import config, incidents
from chatbot.retention import prune_finished


class RetentionTests(unittest.TestCase):
    def test_pruning_keeps_pending_records_and_removes_oldest_completed(self):
        records = [{"id": 1, "done": False}, {"id": 2, "done": True}, {"id": 3, "done": True}]
        removed = prune_finished(records, 2, lambda item: item["done"])
        self.assertEqual(removed, 1)
        self.assertEqual([item["id"] for item in records], [1, 3])
        self.assertEqual(prune_finished(records, 1, lambda item: False), 0)

    def test_report_read_can_progress_while_vehicle_lookup_is_blocked(self):
        started = threading.Event()
        release = threading.Event()

        class Data:
            def list_cars(self, lot_id):
                started.set()
                if not release.wait(5):
                    raise AssertionError("report reader did not release vehicle lookup")
                return []

        incidents.reset()
        self.addCleanup(incidents.reset)
        lot = config.catalog.ids()[0]
        with ThreadPoolExecutor(2) as pool:
            observe = pool.submit(incidents.observe, {"level": 1, "lot_id": lot, "spot_no": 1, "version": 1}, Data())
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(pool.submit(incidents.list_public).result(timeout=2), [])
            finally:
                release.set()
            self.assertEqual(observe.result(timeout=3), 1)
