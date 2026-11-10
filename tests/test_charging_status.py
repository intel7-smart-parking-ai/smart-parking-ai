"""가상 충전 상태(D-66 PM 버전, D-67 A안): C·D 시연만, A·B 실물은 충전 정보를 만들지 않는다.

#78(김채린) telemetry 테스트의 의도를 main 형식(charging_status)으로 옮긴 것.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.runtime import create_runtime_app
from backend.app.simulation.charging import (CHARGE_SECONDS, CONNECT_SECONDS, RATED_POWER_KW,
                                             charging_status, last_charge_end)

NOW = datetime(2026, 11, 10, 3, 0, 0, tzinfo=timezone.utc)


def ev_row(seconds_since_entry=60, **kw):
    row = {"space_type": "ev", "status": "occupied", "vehicle_type": "ev", "session_id": "s-1",
           "entry_at": (NOW - timedelta(seconds=seconds_since_entry)).isoformat()}
    row.update(kw)
    return row


def test_empty_bay_is_available_with_last_use():
    data = charging_status(ev_row(status="empty", session_id=None, entry_at=None), NOW, "2026-11-10T02:50:00+00:00")
    assert data["status"] == "available"
    assert data["is_mock"] is True and data["rated_power_kw"] == RATED_POWER_KW
    assert data["last_used_at"] == "2026-11-10T02:50:00+00:00"


def test_uncertain_or_unavailable_bay_is_unknown():
    for status in ("uncertain", "unavailable"):
        assert charging_status(ev_row(status=status), NOW)["status"] == "unknown"


def test_non_ev_vehicle_is_not_connected():
    assert charging_status(ev_row(vehicle_type="general"), NOW)["status"] == "not_connected"


def test_occupied_without_session_is_unknown():
    assert charging_status(ev_row(session_id=None), NOW)["status"] == "unknown"
    assert charging_status(ev_row(entry_at=None), NOW)["status"] == "unknown"


def test_cycle_connected_charging_completed():
    connected = charging_status(ev_row(CONNECT_SECONDS - 5), NOW)
    charging = charging_status(ev_row(CONNECT_SECONDS + 60), NOW)
    done = charging_status(ev_row(CONNECT_SECONDS + CHARGE_SECONDS + 5), NOW)
    assert connected["status"] == "connected" and connected["power_kw"] == 0
    assert 20 <= connected["battery_percent"] <= 80
    assert charging["status"] == "charging" and charging["power_kw"] == RATED_POWER_KW
    assert connected["battery_percent"] <= charging["battery_percent"] < 100
    assert charging["remaining_seconds"] == CHARGE_SECONDS - 60
    assert done["status"] == "completed" and done["battery_percent"] == 100 and done["power_kw"] == 0


def test_same_session_keeps_same_start_battery():
    a = charging_status(ev_row(5), NOW)["battery_percent"]
    b = charging_status(ev_row(5), NOW)["battery_percent"]
    assert a == b


def test_last_charge_end_before_connect_is_none():
    entry = NOW.isoformat()
    assert last_charge_end(entry, (NOW + timedelta(seconds=CONNECT_SECONDS - 1)).isoformat()) is None
    assert last_charge_end(entry, (NOW + timedelta(seconds=CONNECT_SECONDS + 30)).isoformat()) == \
        (NOW + timedelta(seconds=CONNECT_SECONDS + 30)).isoformat()


def test_real_lots_never_report_charging(tmp_path):
    """A·B(실물)는 충전기 데이터가 없으므로 빈 전기차 칸이어도 '충전 가능'으로 만들지 않는다."""
    app = create_runtime_app(token="charging-test-token", database=tmp_path / "charging.db", mode="demo")
    with TestClient(app) as client:
        for lot in ("A", "B"):
            spaces = client.get(f"/api/v1/parking-lots/{lot}/spaces").json()["items"]
            ev = [s for s in spaces if s["space_type"] == "ev"]
            assert ev, f"{lot} 주차장에 전기차 칸이 있어야 시험이 의미 있음"
            assert all(s.get("charging") is None for s in ev)
        virtual = [s for lot in ("C", "D")
                   for s in client.get(f"/api/v1/parking-lots/{lot}/spaces").json()["items"] if s["space_type"] == "ev"]
        assert virtual and all(s["charging"]["is_mock"] is True for s in virtual)
