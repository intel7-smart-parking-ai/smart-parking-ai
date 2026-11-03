"""요금·모의결제·매출 API 통합 테스트 (KAN-28, 담당: 윤명수).

각 테스트는 tmp_path의 임시 SQLite DB를 쓴다(저장소에 DB 파일을 만들지 않음).
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.billing.calculator import calculate_parking_fee_pure
from backend.app.billing.db import init_schema
from fastapi import FastAPI

from backend.app.api.billing_router import billing_router

# main.py 등록은 DB 방식(KAN-17) 확정 후 박해준 님과 협의. 그 전까지 billing 전용 앱으로 검증한다.
app = FastAPI()
app.include_router(billing_router)

POLICY = {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SMART_PARKING_DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.executemany(
        "INSERT INTO parking_lots (lot_id, name, type, total_spaces) VALUES (?, ?, ?, ?)",
        [("A", "실물 A", "real", 16), ("B", "실물 B", "real", 12), ("C", "가상 C", "virtual", 10)],
    )
    conn.execute(
        "INSERT INTO rate_policies VALUES ('A', ?, ?, ?, ?, ?)",
        tuple(POLICY[k] for k in ("base_minutes", "base_fee", "unit_minutes", "extra_fee", "daily_cap")),
    )
    conn.executemany(
        "INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, exit_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("ses_paid", "A", "A-01", "01", "2026-11-02T09:05:00+09:00", "2026-11-02T10:00:00+09:00"),  # 55분 → 2,500
            ("ses_nocar", "A", "A-02", None, "2026-11-02T09:00:00+09:00", "2026-11-02T09:30:00+09:00"),  # 30분 → 1,000
            ("ses_active", "A", "A-03", "09", "2026-11-02T09:00:00+09:00", None),
        ],
    )
    conn.commit()
    conn.close()
    with TestClient(app) as c:
        yield c


def _pay(client, session_id="ses_paid", key="idem-1", **extra):
    return client.post("/api/v1/mock-payments", json={"session_id": session_id, "idempotency_key": key, **extra})


# ---------- 요금 계산 (순수 함수) ----------

@pytest.mark.parametrize(
    "minutes, expected",
    [(1, 1000), (30, 1000), (31, 1500), (40, 1500), (41, 2000), (55, 2500), (600, 10000), (1440, 10000)],
)
def test_fee_boundaries(minutes, expected):
    entry = "2026-11-02T00:00:00+09:00"
    from datetime import datetime, timedelta
    target = (datetime.fromisoformat(entry) + timedelta(minutes=minutes)).isoformat()
    assert calculate_parking_fee_pure(entry, target, POLICY)["amount_krw"] == expected


# ---------- 요금 조회 ----------

def test_fee_lookup(client):
    r = client.get("/api/v1/sessions/ses_paid/fee")
    assert r.status_code == 200
    body = r.json()
    assert body["amount_krw"] == 2500
    assert body["duration_minutes"] == 55
    assert body["car_id"] == "01"  # 선행 0 보존
    assert body["payment_status"] == "unpaid"


def test_fee_unrecognized_car_is_null(client):
    r = client.get("/api/v1/sessions/ses_nocar/fee")
    assert r.status_code == 200
    assert r.json()["car_id"] is None


def test_fee_active_session_uses_now_param(client):
    r = client.get("/api/v1/sessions/ses_active/fee", params={"now": "2026-11-02T09:41:00+09:00"})
    assert r.status_code == 200
    assert r.json()["amount_krw"] == 2000  # 41분


def test_fee_unknown_session_404(client):
    r = client.get("/api/v1/sessions/nope/fee")
    assert r.status_code == 404
    assert r.json()["error_code"] == "SESSION_NOT_FOUND"


# ---------- 모의 결제 ----------

def test_payment_amount_is_server_calculated(client):
    r = _pay(client)
    assert r.status_code == 200
    body = r.json()
    assert body["amount_krw"] == 2500
    assert body["status"] == "completed"
    assert body["car_id"] == "01"


def test_payment_rejects_client_amount(client):
    r = _pay(client, amount_krw=1)
    assert r.status_code == 422


def test_payment_unknown_session_404_and_not_saved(client):
    r = _pay(client, session_id="nope", key="idem-x")
    assert r.status_code == 404
    rev = client.get("/api/v1/reports/revenue").json()
    assert rev["summary"]["completed_count"] == 0


def test_payment_same_idempotency_key_replays(client):
    first = _pay(client).json()
    second = _pay(client)
    assert second.status_code == 200
    assert second.json()["payment_id"] == first["payment_id"]
    assert second.json()["idempotent_replayed"] is True
    assert client.get("/api/v1/reports/revenue").json()["summary"]["completed_count"] == 1


def test_payment_different_key_same_session_409(client):
    _pay(client)
    r = _pay(client, key="idem-2")
    assert r.status_code == 409
    assert r.json()["error_code"] == "ALREADY_PAID"


def test_payment_unrecognized_car(client):
    r = _pay(client, session_id="ses_nocar", key="idem-nocar")
    assert r.status_code == 200
    assert r.json()["car_id"] is None
    assert r.json()["amount_krw"] == 1000


# ---------- 취소 · 매출 ----------

def test_cancel_adjusts_revenue_and_cannot_repeat(client):
    pay = _pay(client).json()
    before = client.get("/api/v1/reports/revenue").json()["summary"]["net_revenue_krw"]

    r = client.post(f"/api/v1/mock-payments/{pay['payment_id']}/cancel", json={"refund_reason": "테스트 환불"})
    assert r.status_code == 200
    assert r.json()["status"] == "canceled"

    after = client.get("/api/v1/reports/revenue").json()["summary"]
    assert before - after["net_revenue_krw"] == 2500
    assert after["canceled_amount_krw"] == 2500

    again = client.post(f"/api/v1/mock-payments/{pay['payment_id']}/cancel")
    assert again.status_code == 400
    assert again.json()["error_code"] == "INVALID_PAYMENT_STATE"


def test_cancel_unknown_payment_404(client):
    r = client.post("/api/v1/mock-payments/pay_nope/cancel")
    assert r.status_code == 404


def test_pay_again_after_cancel(client):
    pay = _pay(client).json()
    client.post(f"/api/v1/mock-payments/{pay['payment_id']}/cancel")
    r = _pay(client, key="idem-retry")
    assert r.status_code == 200


def test_revenue_report_shape(client):
    _pay(client)
    r = client.get("/api/v1/reports/revenue")
    assert r.status_code == 200
    body = r.json()
    for key in ("summary", "lots", "daily_revenue", "monthly_revenue"):
        assert key in body
    lot_a = next(l for l in body["lots"] if l["lot_id"] == "A")
    assert lot_a["net_revenue_krw"] == 2500
