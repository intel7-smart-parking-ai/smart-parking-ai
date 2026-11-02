"""정리본 검사: 칩 정리, 문의 상태 추적 끄기, 시연 도구, 문구.

기준: EPIC06_최종수정지시서.md, EPIC06_기능정리_지시서.md (문의 상태 추적은 삭제 대신 끄기)
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import engine as e, config
from chatbot.adapters import CachedAdapter, MockAdapter


def ids(r):
    return [c["id"] for c in r["chips"]]


def all_chip_ids(r):
    return ids(r) + [c["id"] for c in r.get("actions") or []]


class FinalCleanupTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop)
        e.reset_all()

    def ack_all(self, sid, cursor=0):
        r = e.updates(sid, cursor=cursor)
        return r, r.get("cursor", cursor)

    # ---------------------------------------------------------- 1-A 대기 칩
    def test_no_wait_chip_when_alternatives_exist(self):
        r = e.handle("s", text="B 주차장 자리 있어?")  # B 만차, C·D 여유
        self.assertTrue(any(i.startswith("go:") for i in ids(r)))
        self.assertFalse(any(i.startswith("wait") for i in ids(r)))

    def test_zone_full_has_no_zone_wait_chip(self):
        r = e.handle("s", text="B 충전 구역 자리 있어?")
        self.assertTrue(any(i.startswith("go:") for i in ids(r)))
        self.assertFalse(any(i.startswith("wait") for i in ids(r)))

    def test_all_full_offers_current_lookup_and_admin(self):
        for lot in e.data.inner.capacity: e.data.fill_lot(lot)
        r = e.handle("s", text="C 주차장 자리 있어?")
        self.assertIn("empty_all", ids(r))
        self.assertIn("call_admin", ids(r))
        self.assertFalse(any(i.startswith("go:") for i in ids(r)))

    def test_subscription_request_returns_honest_current_lookup(self):
        r = e.handle("s", text="B 자리 나면 알려줘")
        self.assertIn("알려드리는 기능은 없어요", r["text"])
        self.assertEqual(r["card"]["type"], "empty_spots")

    def test_destination_change_notice_has_no_wait_chip_when_alternatives(self):
        e.handle("s", text="D로 안내해 줘")
        _, cur = self.ack_all("s")
        e.data.fill_lot("D")
        r, _ = self.ack_all("s", cur)
        msgs = [m for m in r["messages"] if m.get("meta", {}).get("notification") == "destination"]
        self.assertEqual(len(msgs), 1)
        chips = all_chip_ids(msgs[0])
        self.assertTrue(any(i.startswith("go:") for i in chips))
        self.assertFalse(any(i.startswith("wait") for i in chips))
        self.assertIn("destination_cancel", chips)  # 목적지가 있으니 보임
        self.assertIn("만차가 됐어요", msgs[0]["text"])

    # ---------------------------------------------------------- 1-B 이용 현황
    def test_usage_question_returns_location(self):
        e.handle("s", text="7번 차 위치"); e.handle("s", text="네")
        r = e.handle("s", text="내 이용 현황 보여줘")
        self.assertEqual(r["card"]["type"], "car_location")

    # ---------------------------------------------------------- 목적지 취소 칩
    def test_destination_cancel_hidden_without_destination(self):
        for text in ("B 주차장 자리 있어?", "C 주차장 자리 있어?", "B로 갈게"):
            r = e.handle("s", text=text)
            self.assertIsNone(r["destination"])
            self.assertNotIn("destination_cancel", ids(r), text)

    def test_destination_cancel_shown_with_destination(self):
        r = e.handle("s", text="D로 안내해 줘")
        self.assertEqual(r["destination"]["lot_id"], "D")
        self.assertIn("destination_cancel", ids(r))

    # ---------------------------------------------------------- 2 문의 상태 추적 끄기
    def test_receipt_lookup_has_no_state_changes(self):
        e.handle("s", text="관리자 불러줘")
        r = e.handle("s", text="내 문의 상태")
        self.assertIn("접수돼 있어요", r["text"])
        self.assertEqual(len(e.admin_calls()), 1)

    def test_merge_within_window_then_new_after(self):
        e.handle("s", text="7번 차 위치"); e.handle("s", text="네")
        e.handle("s", text="관리자 불러줘")
        r = e.handle("s", text="관리자 불러줘")
        self.assertIn("이미 접수된 문의", r["text"])
        self.assertEqual(len(e.admin_calls()), 1)
        e.ADMIN_CALLS[-1]["_last_at"] -= config.CALL_MERGE_SEC + 60  # 11분 경과
        r = e.handle("s", text="관리자 불러줘")
        self.assertIn("확인을 요청했어요", r["text"])
        self.assertEqual(len(e.admin_calls()), 2)

    def test_alert_recall_still_creates_linked_call(self):
        e.handle("s", text="8번 차 위치"); e.handle("s", text="네")
        e.handle("s", text="관리자 불러줘")
        e.data.set_alert(2, "A", 6); e.updates("s")
        e.handle("s", text="불났어요 관리자 불러주세요")
        self.assertEqual(len(e.admin_calls()), 2)
        from chatbot import incidents
        self.assertIn(2, incidents.list_public()[0]["admin_calls"])

    def test_status_question_never_creates_call(self):
        r = e.handle("s", text="관리자 확인했나요?")
        self.assertIn("접수된 관리자 문의가 없어요", r["text"])
        self.assertEqual(len(e.admin_calls()), 0)
        e.handle("s", text="관리자 불러줘")
        for text in ("관리자 확인했나요?", "관리자 언제 오나요", "관리자 아직 안 왔어요"):
            r = e.handle("s", text=text)
            self.assertIn("접수돼 있어요", r["text"], text)
        self.assertEqual(len(e.admin_calls()), 1)

    # ---------------------------------------------------------- 시연 도구
    def test_demo_enter_fill_clear_and_vehicle(self):
        before = e.data.get_empty_spots()["lots"]["D"]["empty"]
        r = e.data.enter_car("D")
        self.assertTrue(r["ok"] and r["demo"])
        self.assertEqual(e.data.get_empty_spots()["lots"]["D"]["empty"], before - 1)
        e.data.fill_lot("D")
        self.assertEqual(e.data.get_empty_spots()["lots"]["D"]["empty"], 0)
        self.assertEqual(e.data.enter_car("D")["reason"], "만차")
        e.data.clear_demo("D")
        self.assertEqual(e.data.get_empty_spots()["lots"]["D"]["empty"], before)
        r = e.data.enter_car("C", "04")
        self.assertTrue(r["ok"])
        self.assertEqual(e.data.find_car("04")["lot_id"], "C")
        self.assertEqual(e.data.enter_car("C", "04")["reason"], "이미 주차 중")
        # 시연용 점유는 차량 목록(영향 차량)에 섞이지 않는다
        self.assertTrue(all(c["vehicle_no"].isdigit() for c in e.data.list_cars()))

    def test_demo_restriction_and_wording(self):
        e.data.set_restriction("C", True, "점검 중")
        r = e.handle("s", text="C 주차장 자리 있어?")
        self.assertIn("C 주차장은 지금 이용할 수 없어요. (점검 중)", r["text"])
        self.assertNotIn("운영 제한:", r["text"])
        e.data.set_restriction("C", False)
        r = e.handle("s", text="C 주차장 자리 있어?")
        self.assertIn("자리 있어요", r["text"])

    def test_full_demo_sequence(self):
        """최종 수정 지시서 수정 1의 시연 순서."""
        r = e.handle("s", text="D로 안내해 줘")
        self.assertEqual(r["destination"]["lot_id"], "D")
        _, cur = self.ack_all("s")
        e.data.fill_lot("D")
        r, cur = self.ack_all("s", cur)
        notice = [m for m in r["messages"] if m.get("meta", {}).get("notification") == "destination"][0]
        self.assertIn("go:C", all_chip_ids(notice))
        r = e.handle("s", chip_id="go:C")
        self.assertEqual(r["destination"]["lot_id"], "C")
        _, cur = self.ack_all("s", cur)
        e.data.set_restriction("C", True, "점검 중")
        r, cur = self.ack_all("s", cur)
        notice = [m for m in r["messages"] if m.get("meta", {}).get("notification") == "destination"][0]
        self.assertIn("C 주차장을 지금 이용할 수 없게 됐어요. (점검 중)", notice["text"])
        e.data.set_restriction("C", False)
        e.handle("s", text="4번 차 위치")  # 아직 입차 전
        e.handle("s", text="네")
        e.data.enter_car("A", "04")
        r = e.updates("s", cursor=cur)
        self.assertIsNone(r["destination"])  # 입차 확인으로 감시 종료

    # ---------------------------------------------------------- 경보 문구
    def test_missed_alert_history_uses_plain_language(self):
        e.handle("s", event="start")
        _, cur = self.ack_all("s")
        e.data.set_alert(2, "D", 3)
        e.updates("s", cursor=cur)       # 경보 알림이 큐에 들어감 (화면 반영 전)
        e.data.set_alert(0)
        r = e.updates("s", cursor=cur)   # 받지 못한 채 해제
        texts = " / ".join(m["text"] for m in r["messages"])
        self.assertNotIn("서버가 관찰", texts)
        self.assertNotIn("Lv", texts)
        self.assertEqual(len(r["messages"]), 1, texts)  # 이력 + 해제를 한 메시지로
        self.assertIn("잠시 D 주차장에 경보가 있었고 지금은 해제됐어요.", texts)


if __name__ == "__main__":
    unittest.main()
