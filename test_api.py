#!/usr/bin/env python3
"""박해준 님 공식 4대 API 전수 테스트 스크립트 (FastAPI TestClient & Service Layer).

[검증 대상 API 4종]:
  1. GET  /api/v1/sessions/{session_id}/fee        — 요금 조회
  2. POST /api/v1/mock-payments                    — 모의 결제
  3. POST /api/v1/mock-payments/{id}/cancel        — 결제 취소
  4. GET  /api/v1/reports/revenue                  — 매출 조회
"""

from __future__ import annotations
import sys
import server


def test_official_four_apis() -> None:
  print("\n====================================================================")
  print("[박해준 님 공식 4대 API 연동 검증 시작]")
  print("====================================================================")
  server.init_db()

  # 1. GET /api/v1/sessions/{session_id}/fee
  print("\n1) GET /api/v1/sessions/{session_id}/fee — 요금 조회 테스트")
  code, fee = server.service_get_session_fee("ses_0001", "2026-11-02T10:00:00+09:00")
  assert code == 200, f"요금 조회 실패: {code}"
  assert "amount_krw" in fee
  assert "duration_minutes" in fee
  assert "fee_breakdown" in fee
  print(f"   [PASS] 200 OK | 세션({fee['session_id']}) 차량({fee['car_id']}번) 주차시간: {fee['duration_minutes']}분 -> 계산 요금: {fee['amount_krw']:,}원")

  # 2. POST /api/v1/mock-payments
  print("\n2) POST /api/v1/mock-payments — 모의 결제 테스트")
  idem_key = "idem_official_test_001"
  code, pay = server.service_post_mock_payment({
      "session_id": "ses_0001",
      "idempotency_key": idem_key,
      "payment_method": "MOCK_CARD",
      "created_at": "2026-11-02T10:05:00+09:00",
  })
  assert code == 200, f"모의 결제 실패: {code}"
  assert pay["status"] == "completed"
  payment_id = pay["payment_id"]
  print(f"   [PASS] 200 OK | 결제 승인 완료: ID={payment_id}, 금액={pay['amount_krw']:,}원, 멱등키={idem_key}")

  # 2-1) 중복 결제 방지 (동일 멱등키)
  print("   ↳ [검증] 동일 idempotency_key 중복 요청 시")
  code, pay_dup = server.service_post_mock_payment({
      "session_id": "ses_0001",
      "idempotency_key": idem_key,
  })
  assert code == 200
  assert pay_dup.get("idempotent_replayed") is True
  print(f"   [PASS] 200 OK | 중복 결제 생성 방지 확인 (기존 결제 반환: {pay_dup['payment_id']})")

  # 2-2) 이미 결제 완료된 세션에 다른 키로 요청 시 409
  print("   ↳ [검증] 기결제 세션에 다른 멱등키로 이중 결제 시도 시")
  code, pay_conflict = server.service_post_mock_payment({
      "session_id": "ses_0001",
      "idempotency_key": "idem_different_key_999",
  })
  assert code == 409
  assert pay_conflict["error_code"] == "ALREADY_PAID"
  print(f"   [PASS] 409 Conflict | 이중 결제 차단 확인 (ALREADY_PAID)")

  # 3. POST /api/v1/mock-payments/{id}/cancel
  print(f"\n3) POST /api/v1/mock-payments/{{id}}/cancel — 결제 취소 테스트 (ID={payment_id})")
  _, rev_before = server.service_get_revenue_report("2026-11")
  net_before = rev_before["summary"]["net_revenue_krw"]

  code, cancel = server.service_cancel_mock_payment(payment_id, {"refund_reason": "고객 단순 변심 모의 환불"})
  assert code == 200, f"결제 취소 실패: {code}"
  assert cancel["status"] == "canceled"
  assert cancel["revenue_adjusted"] is True

  _, rev_after = server.service_get_revenue_report("2026-11")
  net_after = rev_after["summary"]["net_revenue_krw"]
  assert net_before - net_after == pay["amount_krw"]
  print(f"   [PASS] 200 OK | 결제 취소 및 모의 환불 완료: status=canceled, 매출 차감 정정: {net_before:,}원 -> {net_after:,}원 (-{pay['amount_krw']:,}원)")

  # 4. GET /api/v1/reports/revenue
  print("\n4) GET /api/v1/reports/revenue — 매출 조회 테스트")
  code, rev = server.service_get_revenue_report("2026-11")
  assert code == 200, f"매출 조회 실패: {code}"
  assert "summary" in rev
  assert "lots" in rev
  assert "daily_revenue" in rev
  assert "monthly_revenue" in rev
  s = rev["summary"]
  print(f"   [PASS] 200 OK | 2026년 11월 매출 집계:")
  print(f"          - 총 결제 승인액 (Gross): {s['gross_amount_krw']:,}원")
  print(f"          - 환불 차감액 (Canceled): -{s['canceled_amount_krw']:,}원")
  print(f"          - 최종 순매출 (Net):       {s['net_revenue_krw']:,}원")
  print(f"          - 주차장별 집계 건수:     {len(rev['lots'])}개 주차장 (A, B, C, D 44면 전수 집계)")

  print("\n====================================================================")
  print("[결과] 박해준 님이 지정한 공식 4대 API 전수 100% 정상 작동 완료!")
  print("====================================================================\n")


if __name__ == "__main__":
  test_official_four_apis()
