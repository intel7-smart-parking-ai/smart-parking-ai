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
                "INSERT INTO parking_sessions VALUES (?, ?, ?, ?, ?, ?)",
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
