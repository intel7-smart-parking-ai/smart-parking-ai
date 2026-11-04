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
        [("A", "가", "real", 12), ("B", "나", "real", 16), ("C", "다", "virtual", 12)],
    )
    conn.execute(
        "INSERT INTO rate_policies VALUES ('A', ?, ?, ?, ?, ?)",
        tuple(POLICY[k] for k in ("base_minutes", "base_fee", "unit_minutes", "extra_fee", "daily_cap")),
    )
    conn.executemany(
        "INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, exit_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("ses_paid", "A", "A-01", "27", "2026-11-02T09:05:00+09:00", "2026-11-02T10:00:00+09:00"),  # 55분 → 2,500
            ("ses_nocar", "A", "A-02", None, "2026-11-02T09:00:00+09:00", "2026-11-02T09:30:00+09:00"),  # 30분 → 1,000
            ("ses_active", "A", "A-03", "12", "2026-11-02T09:00:00+09:00", None),
        ],
    )
    conn.commit()
    conn.close()
    with TestClient(app) as c:
        yield c


def _quote(client, session_id="ses_paid", **params):
    """요금 화면 조회 = 견적 발급. 없는 세션이면 존재하지 않는 견적 ID를 돌려준다."""
    r = client.get(f"/api/v1/sessions/{session_id}/fee", params=params)
    return r.json()["quote_id"] if r.status_code == 200 else "quote_missing"


def _pay(client, session_id="ses_paid", key="idem-1", quote_id=None, **extra):
    """화면 흐름 그대로: 요금 조회(견적) → 결제."""
    quote_id = quote_id or _quote(client, session_id)
    return client.post(
        "/api/v1/mock-payments",
        json={"session_id": session_id, "quote_id": quote_id, "idempotency_key": key, **extra},
    )


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
    assert body["car_id"] == "27"  # 두 자리 문자열 그대로
    assert body["payment_status"] == "unpaid"
    assert body["quote_id"].startswith("quote_")
    assert body["quote_expires_at"]


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
    assert r.json()["error"]["code"] == "SESSION_NOT_FOUND"


# ---------- 모의 결제 ----------

def test_payment_amount_is_server_calculated(client):
    r = _pay(client)
    assert r.status_code == 200
    body = r.json()
    assert body["amount_krw"] == 2500
    assert body["status"] == "completed"
    assert body["car_id"] == "27"


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
    assert r.json()["error"]["code"] == "ALREADY_PAID"


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
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_PAYMENT_STATE"


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


def test_revenue_report_lot_filter(client):
    _pay(client)  # A 주차장 2,500원
    a = client.get("/api/v1/reports/revenue", params={"lot_id": "A"}).json()
    assert a["lot_id"] == "A" and a["summary"]["net_revenue_krw"] == 2500
    assert [l["lot_id"] for l in a["lots"]] == ["A"]
    b = client.get("/api/v1/reports/revenue", params={"lot_id": "B"}).json()
    assert b["lot_id"] == "B" and b["summary"]["net_revenue_krw"] == 0 and b["daily_revenue"] == []
    assert client.get("/api/v1/reports/revenue").json()["lot_id"] is None
    r = client.get("/api/v1/reports/revenue", params={"lot_id": "Z"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "PARKING_LOT_NOT_FOUND"


# ---------- 팀 공통 형식 (에러·목록) ----------

def test_error_uses_team_format(client):
    r = client.get("/api/v1/sessions/nope/fee")
    assert set(r.json()) == {"error"}
    assert set(r.json()["error"]) == {"code", "message", "details"}


def test_already_paid_details_has_existing_payment(client):
    first = _pay(client).json()
    r = _pay(client, key="idem-2")
    assert r.json()["error"]["details"] == [{"existing_payment_id": first["payment_id"]}]


def test_same_key_other_session_409(client):
    _pay(client, key="shared-key")
    r = _pay(client, session_id="ses_nocar", key="shared-key")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_list_mock_payments_with_filters(client):
    paid = _pay(client).json()
    _pay(client, session_id="ses_nocar", key="idem-nocar")
    client.post(f"/api/v1/mock-payments/{paid['payment_id']}/cancel")

    body = client.get("/api/v1/mock-payments").json()
    assert set(body) == {"items", "next_cursor", "as_of"}
    assert len(body["items"]) == 2

    canceled = client.get("/api/v1/mock-payments", params={"status": "canceled"}).json()["items"]
    assert [p["payment_id"] for p in canceled] == [paid["payment_id"]]

    by_car = client.get("/api/v1/mock-payments", params={"car_id": "27"}).json()["items"]
    assert len(by_car) == 1 and by_car[0]["car_id"] == "27"

    assert client.get("/api/v1/mock-payments", params={"status": "cancelled"}).status_code == 422


def test_rate_policies(client):
    body = client.get("/api/v1/rate-policies").json()
    assert set(body) == {"items", "next_cursor", "as_of"}
    assert body["items"] == [{"lot_id": "A", **POLICY}]


# ---------- 주차장 4곳 기본 요금표 (빈 DB 시드) ----------

@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    """데이터가 하나도 없는 DB로 시작 → 첫 연결에서 기본 데이터가 들어간다."""
    monkeypatch.setenv("SMART_PARKING_DB_PATH", str(tmp_path / "empty.db"))
    with TestClient(app) as c:
        yield c


def test_empty_db_seeds_four_lot_rate_policies(empty_client):
    items = empty_client.get("/api/v1/rate-policies").json()["items"]
    assert [(p["lot_id"], p["base_fee"], p["extra_fee"], p["daily_cap"]) for p in items] == [
        ("A", 1000, 500, 10000),
        ("B", 2000, 700, 20000),
        ("C", 3000, 800, 40000),
        ("D", 4000, 1000, 50000),
    ]
    assert all(p["base_minutes"] == 30 and p["unit_minutes"] == 10 for p in items)


def test_seeded_lots_appear_in_revenue_report(empty_client):
    lots = {l["lot_id"]: l for l in empty_client.get("/api/v1/reports/revenue").json()["lots"]}
    assert {k: (v["name"], v["total_spaces"]) for k, v in lots.items()} == {
        "A": ("개발원주차장", 12), "B": ("부산역주차장", 16), "C": ("북항주차장", 12), "D": ("신세계백화점주차장", 64),
    }


def test_seed_does_not_touch_existing_data(client):
    """이미 주차장 데이터가 있으면 기본 데이터를 넣지 않는다 (기존 테스트 DB의 A 정책만 유지)."""
    assert [p["lot_id"] for p in client.get("/api/v1/rate-policies").json()["items"]] == ["A"]


@pytest.mark.parametrize(
    "lot, minutes, expected",
    [("B", 30, 2000), ("B", 41, 3400), ("C", 31, 3800), ("D", 55, 7000), ("D", 600, 50000)],
)
def test_lot_specific_fees(lot, minutes, expected):
    """주차장별 요금표로 계산 (기본 30분, 10분 단위 올림, 일 최대)."""
    from datetime import datetime, timedelta
    from backend.app.billing.db import DEFAULT_RATE_POLICIES

    row = next(r for r in DEFAULT_RATE_POLICIES if r[0] == lot)
    policy = dict(zip(("base_minutes", "base_fee", "unit_minutes", "extra_fee", "daily_cap"), row[1:]))
    entry = "2026-11-02T00:00:00+09:00"
    target = (datetime.fromisoformat(entry) + timedelta(minutes=minutes)).isoformat()
    assert calculate_parking_fee_pure(entry, target, policy)["amount_krw"] == expected


# ---------- 실서버 조건 (스레드·동시성) ----------

def test_db_conn_usable_from_other_thread(tmp_path, monkeypatch):
    """uvicorn은 의존성과 엔드포인트를 다른 스레드에서 실행할 수 있다 (실서버 500 재현 방지)."""
    import threading
    from backend.app.billing.db import get_db_conn

    monkeypatch.setenv("SMART_PARKING_DB_PATH", str(tmp_path / "t.db"))
    gen = get_db_conn()
    conn = next(gen)
    errors = []

    def use():
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=use)
    t.start()
    t.join()
    gen.close()
    assert errors == []


def test_concurrent_payments_only_one_succeeds(client):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(10) as ex:
        results = list(ex.map(lambda i: _pay(client, key=f"race-{i}").status_code, range(10)))
    assert results.count(200) == 1
    assert set(results) <= {200, 409}


# ---------- 요금 견적 quote_id (DECISIONS D-09) ----------

def test_payment_charges_quoted_amount_not_recalculated(client):
    """요금 화면(견적)에 보인 금액으로 결제한다. 결제 시점에 다시 계산하면 일 최대 10,000원이 된다."""
    quote_id = _quote(client, "ses_active", now="2026-11-02T09:41:00+09:00")  # 41분 → 2,000원
    r = _pay(client, session_id="ses_active", key="idem-quote", quote_id=quote_id)
    assert r.status_code == 200
    assert r.json()["amount_krw"] == 2000


def test_payment_requires_quote_id(client):
    r = client.post("/api/v1/mock-payments", json={"session_id": "ses_paid", "idempotency_key": "idem-noquote"})
    assert r.status_code == 422


def test_payment_unknown_quote_404(client):
    r = _pay(client, key="idem-unknown-quote", quote_id="quote_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "QUOTE_NOT_FOUND"


def test_payment_quote_of_other_session_409(client):
    other = _quote(client, "ses_nocar")
    r = _pay(client, key="idem-mismatch", quote_id=other)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "QUOTE_MISMATCH"
    assert client.get("/api/v1/reports/revenue").json()["summary"]["completed_count"] == 0


def test_payment_expired_quote_409(client, monkeypatch):
    monkeypatch.setenv("FEE_QUOTE_TTL_SECONDS", "0")  # 발급 즉시 만료
    r = _pay(client, key="idem-expired")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "QUOTE_EXPIRED"
    assert client.get("/api/v1/reports/revenue").json()["summary"]["completed_count"] == 0


def test_idempotent_replay_works_after_quote_expired(client, tmp_path):
    """네트워크 재시도: 같은 멱등키면 견적이 만료됐어도 기존 결제를 그대로 돌려준다."""
    quote_id = _quote(client)
    first = _pay(client, key="idem-replay", quote_id=quote_id).json()
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.execute("UPDATE fee_quotes SET expires_at = '2000-01-01T00:00:00+09:00' WHERE quote_id = ?", (quote_id,))
    conn.commit()
    conn.close()
    again = _pay(client, key="idem-replay", quote_id=quote_id)
    assert again.status_code == 200
    assert again.json()["payment_id"] == first["payment_id"]
    assert again.json()["idempotent_replayed"] is True
