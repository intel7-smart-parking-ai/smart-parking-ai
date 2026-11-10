from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import Mock

import numpy as np

from web_live.live import LiveSession
from web_live.pipeline import public_result
from web_live.policy import to_events, without_duplicate_identities
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually


class CrossCameraIdentityTests(unittest.TestCase):
    def test_local_conflict_also_blocks_other_camera_and_queued_identity(self):
        a, pa = fixture("A")
        b, pb = fixture("B")
        a["_duplicate_ids"] = ("23",)
        for row in a["spaces"][:2]:
            row.update(observation="occupied", car_id=None, vehicle_type="ev", number_candidate="23")
        b["spaces"][0].update(observation="occupied", car_id="23", vehicle_type="ev")
        b["detections"] = [dict(space_id=b["spaces"][0]["space_id"], car_id="23")]
        live = LiveSession(Pipeline(), transport_factory=Transport)
        self.addCleanup(live.close)
        live.running = live.sending = True
        live.hub = Hub([])
        live.results = {"1": a, "2": b}
        live.packets = live.hub.packets = {"1": pa, "2": pb}
        raw_event = to_events(b, pb, "camera_b")[0]
        pa["received"] = pb["received"] = time.monotonic()
        visible = without_duplicate_identities(live.results)["2"]
        self.assertIsNone(visible["spaces"][0]["car_id"])
        self.assertIsNone(visible["detections"][0]["car_id"])
        self.assertNotIn("_duplicate_ids", public_result(a))
        held = to_events(visible, pb, "camera_b")[0]
        self.assertEqual((held["event_type"], held["car_id"], held["vehicle_type"]), ("vehicle_observed", None, "ev"))
        self.assertFalse(live._valid_event(raw_event, source_camera="2"))
        self.assertTrue(live._valid_event(held, source_camera="2"))
        # Unconfirmed candidates alone must not suppress the other slot.
        a.pop("_duplicate_ids")
        self.assertTrue(live._valid_event(raw_event, source_camera="2"))
        self.assertFalse(live._valid_event(held, source_camera="2"))


class InterruptedReviewTests(unittest.TestCase):
    def check_interruption(self, mode):
        p = Pipeline()
        original_analyze = p.analyze
        p.approve_roi = Mock()

        def analyze(image, **kw):
            r = original_analyze(image, **kw)
            r["_outline"] = np.float32([[0,0],[19,0],[19,19],[0,19]])
            if mode == "gradual":
                # For a 20px image the pose tolerance is .16px.
                # Every adjacent pair passes, but frame 3 left frame 1's tolerance.
                r["_outline"][:, 0] += (0., .1, .2, .1)[kw["sequence"] - 1]
            if kw["sequence"] == 2:
                if mode == "analysis_error":
                    raise RuntimeError("test fault")
                if mode == "unregistered":
                    r["registration_ok"] = False
                if mode == "moved":
                    r["_outline"] += 5
            return r

        p.analyze = analyze
        live = LiveSession(p, discover_fn=lambda: [{}], hub_factory=Hub, transport_factory=Transport)
        self.addCleanup(live.close)
        live.start()

        def feed(seq):
            live.hub.feed(seq=seq)
            self.assertTrue(eventually(lambda: "1" in live.errors if mode == "analysis_error" and seq == 2
                                       else live.results.get("1", {}).get("frame_sequence") == seq))
            packet = live.hub.packets["1"]
            return dict(epoch=packet["epoch"], sequence=seq, observed_at=packet["observed_at"], lot_id="A")

        old = feed(1)
        feed(2)
        fresh = feed(3)
        if mode == "gradual":
            fresh = feed(4)
        with self.assertRaises(ValueError):
            live.confirm("1", reviewed=old)
        live.confirm("1", reviewed=fresh)
        self.assertEqual(p.approve_roi.call_count, 1)

    def test_registration_loss(self):
        self.check_interruption("unregistered")

    def test_analysis_error(self):
        self.check_interruption("analysis_error")

    def test_board_moves_then_returns(self):
        self.check_interruption("moved")

    def test_gradual_motion_past_review_tolerance(self):
        self.check_interruption("gradual")

    def test_review_cannot_overtake_publication_of_failed_registration(self):
        p = Pipeline()
        entered, release = threading.Event(), threading.Event()
        lost = threading.Event()
        original_analyze = p.analyze
        def analyze(image, **kw):
            r = original_analyze(image, **kw)
            r["_outline"] = np.float32([[0,0],[19,0],[19,19],[0,19]])
            if kw["sequence"] == 2:
                r["registration_ok"] = False
                lost.set()
            return r
        class PausedHub(Hub):
            def latest(self, camera):
                if lost.is_set() and threading.current_thread().name == "v53-inference":
                    entered.set()
                    release.wait(2)
                return super().latest(camera)

        p.analyze = analyze
        p.approve_roi = Mock()
        live = LiveSession(p, discover_fn=lambda: [{}], hub_factory=PausedHub, transport_factory=Transport)
        self.addCleanup(live.close)
        live.start()
        live.hub.feed(seq=1)
        self.assertTrue(eventually(lambda: bool(live.results)))
        review = dict(epoch=1, sequence=1, observed_at=live.results["1"]["observed_at"], lot_id="A")
        live.hub.feed(seq=2)
        self.assertTrue(entered.wait(1))
        with ThreadPoolExecutor(1) as pool:
            approval = pool.submit(live.confirm, "1", reviewed=review)
            try:
                self.assertTrue(eventually(lambda: live.review_waiters > 0 or approval.done()))
            finally:
                release.set()
            with self.assertRaises(ValueError):
                approval.result(timeout=2)
        p.approve_roi.assert_not_called()
