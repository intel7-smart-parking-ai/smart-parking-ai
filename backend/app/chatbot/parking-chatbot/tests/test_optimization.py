from unittest.mock import patch as _patch
"""기능 유지 최적화: 동시 조회, 실패 복구, 데이터 변경 경합, 중복 조회 방지."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch
from chatbot import engine as e
from chatbot.adapters import CachedAdapter, MockAdapter, DataUnavailable


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(e,"data",CachedAdapter(MockAdapter()))
        p.start();self.addCleanup(p.stop)
        e.reset_all()

    def parallel_read(self, method, *args):
        barrier=threading.Barrier(12)
        def run(_):
            barrier.wait(3)
            return getattr(e.data,method)(*args)
        with ThreadPoolExecutor(max_workers=12)as pool:
            futures=[pool.submit(run,i)for i in range(12)]
            return [f.result(3)for f in futures]

    def test_simultaneous_location_reads_share_one_fetch_but_not_mutable_values(self):
        original=e.data.inner.find_car
        def slow(v):time.sleep(.08);return original(v)
        with patch.object(e.data.inner,"find_car",side_effect=slow)as fetch:
            results=self.parallel_read("find_car","17")
        self.assertEqual(fetch.call_count,1)
        results[0]["spot_no"]=999
        self.assertTrue(all(r["spot_no"]==7 for r in results[1:]))
        self.assertEqual(e.data.find_car("17")["spot_no"],7)

    def test_simultaneous_occupancy_reads_share_one_fetch(self):
        original=e.data.inner.get_empty_spots
        def slow():time.sleep(.08);return original()
        with patch.object(e.data.inner,"get_empty_spots",side_effect=slow)as fetch:
            results=self.parallel_read("get_empty_spots")
        self.assertEqual(fetch.call_count,1)
        self.assertTrue(all(r["lots"]["B"]["empty"]==0 for r in results))

    def test_failed_shared_read_unblocks_everyone_and_next_query_recovers(self):
        barrier=threading.Barrier(12)
        def slow_fail(v):time.sleep(.08);raise OSError("injected outage")
        def run(_):
            barrier.wait(3)
            try:e.data.find_car("17")
            except DataUnavailable:return "unavailable"
            return "unexpected success"
        with patch.object(e.data.inner,"find_car",side_effect=slow_fail)as fetch:
            with ThreadPoolExecutor(max_workers=12)as pool:results=list(pool.map(run,range(12)))
        self.assertEqual(results,["unavailable"]*12)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(e.data.find_car("17")["spot_no"],7)

    def test_sequential_reads_are_fresh_without_new_ttl(self):
        first=e.data.find_car("17")
        e.data.inner.cars["17"]["spot_no"]=4
        second=e.data.find_car("17")
        self.assertEqual((first["spot_no"],second["spot_no"]),(7,4))

    def delayed_mutation(self, action, mutate_other=False):
        entered,release=threading.Event(),threading.Event()
        original=e.data.inner.find_car
        def blocked(v):
            snapshot=original(v);entered.set()
            if not release.wait(3):raise RuntimeError("barrier timeout")
            return snapshot
        with patch.object(e.data.inner,"find_car",side_effect=blocked):
            with ThreadPoolExecutor(max_workers=1)as pool:
                result=pool.submit(e.data.find_car,"17")
                try:
                    self.assertTrue(entered.wait(3));action()
                finally:release.set()
                if mutate_other:
                    self.assertEqual(result.result(3)["spot_no"],7)
                else:
                    with self.assertRaises(DataUnavailable):result.result(3)

    def test_late_location_response_after_exit_cannot_restore_old_cache(self):
        self.delayed_mutation(lambda:e.data.exit_car("17"))
        self.assertIsNone(e.data.find_car("17"))
        self.assertNotIn("car:07",e.data.cache)

    def test_late_location_response_after_reset_is_discarded(self):
        self.delayed_mutation(e.data.reset)
        self.assertNotIn("car:07",e.data.cache)
        self.assertEqual(e.data.find_car("17")["spot_no"],7)

    def test_other_vehicle_exit_does_not_fail_unrelated_location_read(self):
        self.delayed_mutation(lambda:e.data.exit_car("18"),mutate_other=True)

    def test_near_alert_and_pinned_context_use_one_location_read(self):
        e.get_session("s").car_id="17"
        e.data.set_alert(2,"A",8)  # B-02: 17번 차(B-01)의 같은 줄 옆 칸
        with patch.object(e.data,"find_car",wraps=e.data.find_car)as fetch:
            r=e.handle("s",chip_id="alert_near")
        self.assertEqual(fetch.call_count,1)
        self.assertIn("바로 근처",r["text"])
        self.assertEqual(r["vehicle_context"]["spot_no"],7)

    def test_location_failure_is_not_repeated_within_same_request(self):
        e.get_session("s").car_id="17";e.data.set_alert(2,"A",6)
        with patch.object(e.data,"find_car",side_effect=DataUnavailable())as fetch:
            r=e.handle("s",chip_id="alert_near")
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(r["vehicle_context"]["state"],"unavailable")


    def test_request_local_context_is_cleared_between_sessions(self):
        e.get_session("first").car_id="17"
        e.get_session("second").car_id="18"
        first=e.handle("first",chip_id="find_car")
        second=e.handle("second",chip_id="find_car")
        self.assertEqual(first["vehicle_context"]["car_id"],"17")
        self.assertEqual(second["vehicle_context"]["car_id"],"18")
        self.assertIsNone(e._request_session.get())

    def test_registered_receipt_card_renders(self):
        c=e.handle("s",chip_id="call_admin")["card"]["data"]
        r=e.handle("s",chip_id="call_status")
        self.assertEqual(r["card"]["data"]["id"],c["id"])
        self.assertEqual(r["card"]["data"]["status"],"접수")
        self.assertIn("현장 관리자에게 확인", r["text"])
        self.assertEqual(len(e.admin_calls()),1)

