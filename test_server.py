#!/usr/bin/env python3
"""로컬 서버 기능 검증 스크립트 (10대 개발 작업 및 4대 완료 기준 테스트)."""

import server


def run_tests() -> None:
  print("=== [AI 스마트 주차장 로컬 서버 & SQLite DB 검증 시작] ===")
  server.init_db()

  # 1. 차량별 주차요금 계산 검증 (A, B, C 주차장별 정책 적용)
  code, fee_a = server.service_get_session_fee("ses_0001", "2026-11-02T10:00:00+09:00")
  assert code == 200, f"Expected 200, got {code}"
  assert fee_a["duration_minutes"] == 55
  assert fee_a["amount_krw"] == 2500  # 30분 1000원 + 25분(3단위*500원) = 2500원
  print(f"[PASS] 1. A주차장 차량(01번) 55분 요금 계산: {fee_a['amount_krw']:,}원 (기본+추가요금 정확)")

  code, fee_c = server.service_get_session_fee("ses_0004", "2026-11-02T10:00:00+09:00")
  assert code == 200
  assert fee_c["amount_krw"] == 7400  # C주차장: 기본 60분 1000원 + 240분(16단위*400원=6400원) = 7400원 (상한 8000원 이하)
  print(f"[PASS] 2. C주차장 차량(21번) 300분 요금 계산: {fee_c['amount_krw']:,}원 (주차장별 차등 정책 적용)")

  code, fee_d = server.service_get_session_fee("ses_0006", "2026-11-02T10:00:00+09:00")
  assert code == 200
  # D주차장: 입차 08:45 -> 10:00 (75분) -> 기본 30분 800원 + 45분(5단위*300원=1500원) = 2300원
  assert fee_d["amount_krw"] == 2300
  print(f"[PASS] 2-1. D주차장 차량(31번) 75분 요금 계산: {fee_d['amount_krw']:,}원 (가상 D 주차장 10면 정책 적용)")

  # 2. 모의 결제 완료 및 DB 저장 검증
  idem_key = "idem_local_test_ses_0001"
  code, pay1 = server.service_post_mock_payment({
      "session_id": "ses_0001",
      "idempotency_key": idem_key,
      "payment_method": "MOCK_CARD",
      "created_at": "2026-11-02T10:05:00+09:00",
  })
  assert code == 200
  assert pay1["status"] == "completed"
  print(f"[PASS] 3. 모의 결제 완료 및 SQLite DB 저장: {pay1['payment_id']} ({pay1['amount_krw']:,}원)")

  # 3. 중복 결제 방지 (Idempotency) 검증
  code, pay_dup = server.service_post_mock_payment({
      "session_id": "ses_0001",
      "idempotency_key": idem_key,
      "payment_method": "MOCK_CARD",
  })
  assert code == 200
  assert pay_dup.get("idempotent_replayed") is True
  assert pay_dup["payment_id"] == pay1["payment_id"]
  print(f"[PASS] 4. 동일 멱등키({idem_key}) 중복 결제 차단 확인")

  # 4. 다른 키로 기결제 세션 재결제 시 409 차단 검증
  code, pay_conflict = server.service_post_mock_payment({
      "session_id": "ses_0001",
      "idempotency_key": "idem_different_key_999",
  })
  assert code == 409
  assert pay_conflict["error_code"] == "ALREADY_PAID"
  print("[PASS] 5. 기결제 세션 중복 결제 시도 409 ALREADY_PAID 차단 확인")

  # 5. 결제 취소 / 모의 환불 시 매출 정정 검증
  _, rev_before = server.service_get_revenue_report("2026-11")
  net_before = rev_before["summary"]["net_revenue_krw"]

  code, cancel_res = server.service_cancel_mock_payment(
      pay1["payment_id"], {"refund_reason": "로컬 서버 테스트 환불"}
  )
  assert code == 200
  assert cancel_res["status"] == "canceled"

  _, rev_after = server.service_get_revenue_report("2026-11")
  net_after = rev_after["summary"]["net_revenue_krw"]
  assert net_before - net_after == pay1["amount_krw"]
  print(
      f"[PASS] 6. 모의 환불 시 매출 즉시 정정 확인: 환불 전 {net_before:,}원 -> 환불 후 {net_after:,}원 (-{pay1['amount_krw']:,}원 차감)"
  )
  print("=== [모든 로컬 서버 테스트 100% 통과!] ===")


if __name__ == "__main__":
  run_tests()
