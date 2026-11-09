"""Virtual charger telemetry: C/D demo only, never inferred from an empty bay."""
from datetime import datetime, timedelta, timezone

from backend.app.simulation.charging import telemetry

NOW = datetime(2026, 11, 9, 3, 0, 0, tzinfo=timezone.utc)


def ev_row(**kw):
    row = {'space_type': 'ev', 'status': 'occupied', 'vehicle_type': 'ev',
           'session_id': 's1', 'entry_at': (NOW - timedelta(seconds=60)).isoformat()}
    row.update(kw)
    return row


def test_non_ev_bay_has_no_charging_data():
    assert telemetry({'space_type': 'general', 'status': 'occupied'}, NOW, True) is None


def test_real_lots_never_report_available_from_empty_bay():
    # A·B(또는 demo가 아닌 모드)는 충전기 데이터 미연동: 비어 있어도 '즉시 이용 가능'으로 만들지 않는다.
    for status in ('empty', 'occupied', 'uncertain'):
        data = telemetry(ev_row(status=status), NOW, False)
        assert data['status'] == 'unknown'
        assert data['source'] == 'not_connected'
        assert data['is_mock'] is False
        assert data['rated_kw'] is None and data['percent'] is None and data['power_kw'] is None


def test_demo_empty_bay_is_available():
    data = telemetry(ev_row(status='empty', session_id=None, entry_at=None), NOW, True)
    assert data['status'] == 'available' and data['is_mock'] is True and data['rated_kw'] == 7


def test_demo_uncertain_or_unavailable_bay_is_unknown():
    for status in ('uncertain', 'unavailable'):
        assert telemetry(ev_row(status=status), NOW, True)['status'] == 'unknown'


def test_demo_non_ev_vehicle_is_not_connected():
    assert telemetry(ev_row(vehicle_type='general'), NOW, True)['status'] == 'not_connected'


def test_demo_occupied_without_session_is_unknown():
    assert telemetry(ev_row(session_id=None), NOW, True)['status'] == 'unknown'
    assert telemetry(ev_row(entry_at=None), NOW, True)['status'] == 'unknown'


def test_demo_cycle_connected_charging_completed():
    def at(seconds):
        return telemetry(ev_row(entry_at=(NOW - timedelta(seconds=seconds)).isoformat()), NOW, True)

    connected, charging, done = at(10), at(60), at(200)
    assert connected['status'] == 'connected' and connected['power_kw'] == 0.0
    assert charging['status'] == 'charging' and charging['power_kw'] == 7.0
    assert charging['percent'] == 37 and charging['estimated_remaining_seconds'] == 75
    assert done['status'] == 'completed' and done['percent'] == 100
    assert done['estimated_remaining_seconds'] == 0 and done['power_kw'] == 0.0
    assert all(d['is_mock'] and d['source'] == 'simulation' for d in (connected, charging, done))
