import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from backend.app.billing.db import init_schema, seed_defaults
from backend.app.main import app, create_app


def test_default_app_does_not_publish_billing():
    with TestClient(app) as client:
        assert client.get("/api/v1/rate-policies").status_code == 404
        assert "/api/v1/mock-payments" not in client.get("/openapi.json").json()["paths"]


def test_demo_billing_lifecycle(tmp_path):
    path = tmp_path / "demo.sqlite3"
    opened = []

    def demo_connection():
        opened.append(path)
        with closing(sqlite3.connect(path, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    demo = create_app(demo_billing_connection=demo_connection)
    with TestClient(demo) as client:
        assert client.get("/docs").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert set(paths) - set(app.openapi()["paths"]) == {
            "/api/v1/sessions/{session_id}/fee", "/api/v1/mock-payments",
            "/api/v1/mock-payments/{id}/cancel", "/api/v1/rate-policies",
            "/api/v1/reports/revenue",
        }
        assert {"get", "post"} <= set(paths["/api/v1/mock-payments"])
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/parking-lots").status_code == 200
        assert not opened and not path.exists()

        with closing(sqlite3.connect(path)) as conn, conn:
            init_schema(conn)
            seed_defaults(conn)
            conn.execute(
                "INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, exit_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("ses_demo", "A", "A-01", "10", "2026-11-04T09:00:00+09:00",
                 "2026-11-04T09:30:00+09:00"),
            )
        fee = client.get("/api/v1/sessions/ses_demo/fee")
        assert fee.status_code == 200
        payment = client.post("/api/v1/mock-payments", json={
            "session_id": "ses_demo", "quote_id": fee.json()["quote_id"],
            "idempotency_key": "demo-payment",
        })
        assert payment.status_code == 200
        payment_id = payment.json()["payment_id"]
        revenue = "/api/v1/reports/revenue"
        assert client.get(revenue).json()["summary"]["net_revenue_krw"] == fee.json()["amount_krw"]
        canceled = client.post(f"/api/v1/mock-payments/{payment_id}/cancel")
        assert canceled.status_code == 200
        assert client.get(revenue).json()["summary"]["net_revenue_krw"] == 0


def test_free_turnaround_api(tmp_path):
    path = tmp_path / "free.sqlite3"

    def demo_connection():
        with closing(sqlite3.connect(path, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    with closing(sqlite3.connect(path)) as conn, conn:
        init_schema(conn)
        seed_defaults(conn)
        for sid, lot, space, entry, exit_at in (
            ("ses_free_a", "A", "A-01", "2026-11-12T09:00:00+09:00", "2026-11-12T09:04:00+09:00"),
            ("ses_paid_a", "A", "A-02", "2026-11-12T09:00:00+09:00", "2026-11-12T09:06:00+09:00"),
            ("ses_short_c", "C", "A-01", "2026-11-12T09:00:00+09:00", "2026-11-12T09:04:00+09:00"),
            ("ses_long_d", "D", "A-01", "2026-11-12T09:00:00+09:00", "2026-11-12T09:06:00+09:00"),
        ):
            conn.execute(
                "INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, exit_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (sid, lot, space, None, entry, exit_at))

    demo = create_app(demo_billing_connection=demo_connection)
    with TestClient(demo) as client:
        policies = {p["lot_id"]: p for p in client.get("/api/v1/rate-policies").json()["items"]}
        assert [policies[lot]["free_minutes"] for lot in "ABCD"] == [5, 5, 5, 5]

        free = client.get("/api/v1/sessions/ses_free_a/fee").json()
        assert free["amount_krw"] == 0 and free["free_applied"] is True
        assert free["payment_status"] == "unpaid"  # 새 결제 상태는 만들지 않는다
        assert client.get("/api/v1/sessions/ses_paid_a/fee").json()["amount_krw"] == 1000
        assert client.get("/api/v1/sessions/ses_short_c/fee").json()["amount_krw"] == 0  # 가상 C도 5분 무료
        assert client.get("/api/v1/sessions/ses_long_d/fee").json()["amount_krw"] > 0

        # 0원 견적으로 결제해도 매출 금액·건수에 들어가지 않는다
        paid = client.post("/api/v1/mock-payments", json={
            "session_id": "ses_free_a", "quote_id": free["quote_id"], "idempotency_key": "free-1"})
        assert paid.status_code == 200 and paid.json()["amount_krw"] == 0
        report = client.get("/api/v1/reports/revenue?lot_id=A").json()
        lot_a = next(l for l in report["lots"] if l["lot_id"] == "A")
        assert report["summary"]["net_revenue_krw"] == 0
        assert report["summary"].get("completed_count", 0) == 0
        assert "5분 이내 무료" in lot_a["policy_summary"]
