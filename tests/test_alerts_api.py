"""경보 API 테스트 — LED 모의 화재 → 경보 목록·확인·해제 (3팀 윤명수·서정범)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.alerts.router import internal_router, router
from backend.app.billing.db import get_db_conn

TOKEN = "alerts-test-token-1234"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ALERTS_INTERNAL_TOKEN", TOKEN)
    path = tmp_path / "alerts.sqlite3"

    def conn_per_request():
        conn = sqlite3.connect(path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    app = FastAPI()
    app.dependency_overrides[get_db_conn] = conn_per_request
    app.include_router(router)
    app.include_router(internal_router)
    with TestClient(app) as c:
        yield c


def fire(client, event_id="evt_1", lot_id="A", space_id="A-03", token=TOKEN, **extra):
    body = {"event_id": event_id, "event_type": "mock_fire_led_observed", "source": "vision",
            "lot_id": lot_id, "camera_id": None if lot_id in "CD" else "camera_a", "space_id": space_id,
            "confidence": 0.9, "observed_at": "2026-11-04T14:00:00+09:00", **extra}
    return client.post("/api/v1/internal/alerts", json=body, headers={"Authorization": f"Bearer {token}"})


def test_led_event_creates_active_mock_fire_alert(client):
    r = fire(client)
    assert r.status_code == 201
    a = r.json()
    assert (a["status"], a["type"], a["level"], a["source"], a["is_mock"]) == ("active", "mock_fire_led", 2, "vision", False)
    assert "모의 화재" in a["message"] and "개발원주차장 A-03" in a["message"]
    assert a["detected_at"] == "2026-11-04T14:00:00+09:00"


def test_list_uses_team_format_for_chatbot(client):
    fire(client, source="simulation")
    body = client.get("/api/v1/alerts").json()
    assert body["next_cursor"] is None and body["as_of"].endswith("+00:00")
    assert body["total"] == 1 and body["active_count"] == 1
    assert body["items"][0]["is_mock"] is True


def test_same_space_is_deduplicated_and_event_id_replay_is_stable(client):
    first = fire(client, "evt_1").json()
    second = fire(client, "evt_2", confidence=0.97)
    assert second.status_code == 200 and second.json()["alert_id"] == first["alert_id"]
    assert second.json()["confidence"] == pytest.approx(0.97)
    replay = fire(client, "evt_1")
    assert replay.status_code == 200 and replay.json()["alert_id"] == first["alert_id"]
    assert client.get("/api/v1/alerts").json()["total"] == 1


def test_acknowledge_resolve_and_retrigger(client):
    alert_id = fire(client, lot_id="C", space_id="C-04").json()["alert_id"]
    ack = client.post(f"/api/v1/alerts/{alert_id}/acknowledge", json={"actor": "윤명수"}).json()
    assert ack["status"] == "acknowledged" and ack["acknowledged_at"]
    res = client.patch(f"/api/v1/alerts/{alert_id}/resolve", json={"actor": "서정범"}).json()
    assert res["status"] == "resolved" and res["acknowledged_at"] == ack["acknowledged_at"]
    again = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_ALERT_TRANSITION"
    new = fire(client, "evt_2", lot_id="C", space_id="C-04")
    assert new.status_code == 201 and new.json()["alert_id"] != alert_id


def test_filters(client):
    fire(client, "evt_a", lot_id="A", space_id="A-01")
    b = fire(client, "evt_b", lot_id="B", space_id="B-02").json()["alert_id"]
    client.post(f"/api/v1/alerts/{b}/acknowledge")
    assert [i["lot_id"] for i in client.get("/api/v1/alerts?lot_id=A").json()["items"]] == ["A"]
    assert [i["alert_id"] for i in client.get("/api/v1/alerts?status=acknowledged").json()["items"]] == [b]


def test_creation_requires_internal_token(client):
    assert fire(client, token="wrong-token-wrong-token").status_code == 403
    assert client.post("/api/v1/alerts", json={}).status_code == 405  # 공개 생성 없음


@pytest.mark.parametrize("bad", [
    {"observed_at": "2026-11-04T14:00:00"},       # 시간대 없음
    {"car_id": "7"},                              # D-06: 10~99
    {"lot_id": "C", "camera_id": "camera_c"},     # 가상 주차장 camera_id는 null
])
def test_invalid_events_rejected(client, bad):
    # 본문 형식은 앱의 검증 오류 처리기를 따른다 (시연 서버는 팀 공통 형식으로 변환)
    assert fire(client, **bad).status_code == 422


def test_not_found_uses_team_error_format(client):
    r = client.get("/api/v1/alerts/alt_none")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "ALERT_NOT_FOUND", "message": "경보(alt_none)를 찾을 수 없습니다.", "details": []}}
