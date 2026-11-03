"""독립 목업 계약·데이터 일치·DB 비접속 확인. 실제 DB를 만들지 않는다."""

from collections import Counter

import socket
from datetime import datetime
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("목업은 PostgreSQL에 접속하면 안 됩니다.")

    monkeypatch.setenv("DATABASE_URL", "invalid-database-url")
    monkeypatch.setenv("TEST_DATABASE_URL", "invalid-test-database-url")
    with TestClient(app) as c:
        # TestClient 시작 후 실제 소켓 접속을 차단한다. DB 드라이버 설치는 필요 없다.
        with monkeypatch.context() as blocked:
            blocked.setattr(socket, "create_connection", forbidden)
            blocked.setattr(socket.socket, "connect", forbidden)
            blocked.setattr(socket.socket, "connect_ex", forbidden)
            yield c


def test_lot_contract(client):
    r = client.get("/api/v1/parking-lots")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "next_cursor", "as_of"}
    assert body["next_cursor"] is None
    assert [(x["lot_id"], x["type"]) for x in body["items"]] == [
        ("A", "real"), ("B", "real"), ("C", "virtual"), ("D", "virtual")]
    for item in body["items"]:
        assert set(item) == {"lot_id", "name", "type", "total_spaces", "empty_spaces",
                             "uncertain_spaces", "unavailable_spaces"}
        assert item['name'] == {'A': '개발원주차장', 'B': '부산역주차장', 'C': '북항주차장', 'D': '신세계백화점주차장'}[item['lot_id']]
    assert sum(x['total_spaces'] for x in body['items']) == 104


@pytest.mark.parametrize("lot", ["A", "B", "C", "D"])
def test_spaces_and_aggregates(client, lot):
    r = client.get(f"/api/v1/parking-lots/{lot}/spaces")
    assert r.status_code == 200
    spaces = r.json()["items"]
    size = {'A': 12, 'B': 16, 'C': 12, 'D': 64}[lot]
    assert len(spaces) == size
    assert len({s["space_id"] for s in spaces}) == size
    counts = Counter(s["status"] for s in spaces)
    assert counts == {"occupied": 1, "empty": size - 3, "uncertain": 1, "unavailable": 1}
    summary = next(x for x in client.get("/api/v1/parking-lots").json()["items"] if x["lot_id"] == lot)
    assert summary["total_spaces"] == sum(counts.values())
    for status in ("empty", "uncertain", "unavailable"):
        assert summary[status + "_spaces"] == counts[status]
    for s in spaces:
        assert set(s) == {"space_id", "status", "car_id", "observed_at", "confidence", "version",
                          'space_type', 'vehicle_type', 'source', 'is_mock'}
        assert s['source'] == 'simulation' and s['is_mock'] is True
        assert s['car_id'] is None or (isinstance(s['car_id'], str) and len(s['car_id']) == 2 and '10' <= s['car_id'] <= '99')
        if s["status"] == "unavailable":
            assert s["observed_at"] is None and s["confidence"] is None
        else:
            assert s["observed_at"] == r.json()["as_of"]
            assert 0 <= s["confidence"] <= 1


@pytest.mark.parametrize("number,lot", [(10, "A"), (20, "B")])
def test_location_matches_occupied_space(client, number, lot):
    car = f"{number:02d}"
    r = client.get(f"/api/v1/vehicles/{car}/location")
    assert r.status_code == 200
    location = r.json()
    assert set(location) == {"car_id", "lot_id", "space_id", "session_id", "status", "observed_at", "confidence",
                             'vehicle_type', 'source', 'is_mock'}
    assert location['vehicle_type'] == 'general' and location['source'] == 'simulation' and location['is_mock'] is True
    assert location["lot_id"] == lot and location["car_id"] == car
    assert location["status"] == "occupied" and location["session_id"] is None
    all_spaces = [s for lot_id in "ABCD" for s in client.get(f"/api/v1/parking-lots/{lot_id}/spaces").json()["items"]]
    matches = [s for s in all_spaces if s["car_id"] == car]
    assert len(matches) == 1
    assert matches[0]["space_id"] == location["space_id"]
    assert matches[0]["observed_at"] == location["observed_at"]
    assert matches[0]["confidence"] == location["confidence"] == 0.95
    assert isinstance(location["car_id"], str) and len(location["car_id"]) == 2


@pytest.mark.parametrize("path,code", [
    ("/api/v1/parking-lots/Z/spaces", "PARKING_LOT_NOT_FOUND"),
    ("/api/v1/parking-lots/unknown/spaces", "PARKING_LOT_NOT_FOUND"),
    ("/api/v1/vehicles/unknown/location", "VEHICLE_NOT_FOUND"),
    ("/api/v1/vehicles/1/location", "VEHICLE_NOT_FOUND"),
    ("/api/v1/vehicles/MOCK-CAR-001/location", "VEHICLE_NOT_FOUND"),
])
def test_missing(client, path, code):
    r = client.get(path)
    assert r.status_code == 404
    assert set(r.json()) == {"error"}
    assert set(r.json()["error"]) == {"code", "message", "details"}
    assert r.json()["error"]["code"] == code
    assert isinstance(r.json()["error"]["message"], str)
    assert r.json()["error"]["details"] == []
    assert r.headers["X-ParkFlow-Data-Source"] == "mock"


@pytest.mark.parametrize("path", ["/api/v1/parking-lots", "/api/v1/parking-lots/A/spaces",
                                      "/api/v1/vehicles/10/location", "/api/v1/vehicles/missing/location"])
def test_mock_header(client, path):
    assert client.get(path).headers["X-ParkFlow-Data-Source"] == "mock"


def test_swagger_and_only_read_operations(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "AI Smart Parking API"
    for path, value in schema["paths"].items():
        if path != "/api/v1/health":
            assert "팀 계약 초안 기준 임시 목업" in value["get"]["description"]
            assert "최종 승인된 계약이 아닙니다" in value["get"]["description"]
    for path in ("/api/v1/parking-lots/{lot_id}/spaces", "/api/v1/vehicles/{car_id}/location"):
        assert "404" in schema["paths"][path]["get"]["responses"]
    assert set(schema["paths"]) == {"/api/v1/health", "/api/v1/parking-lots", "/api/v1/parking-lots/{lot_id}/spaces",
                                    "/api/v1/vehicles/{car_id}/location"}
    assert all(set(value) == {"get"} for value in schema["paths"].values())


def test_responses_are_repeatable(client):
    before = client.get("/api/v1/parking-lots/A/spaces").json()
    before["items"][0]["car_id"] = "CLIENT-LOCAL-EDIT"
    assert client.get("/api/v1/parking-lots/A/spaces").json()["items"][0]["car_id"] == "10"


def test_existing_health_is_preserved(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "service", "timestamp"}
    assert body["status"] == "ok"
    assert body["service"] == "smart-parking-api"
    assert datetime.fromisoformat(body["timestamp"]).utcoffset().total_seconds() == 0
    assert "X-ParkFlow-Data-Source" not in response.headers


def test_non_mock_routes_have_no_mock_header(client):
    for path in ("/docs", "/openapi.json", "/api/v1/not-found"):
        assert "X-ParkFlow-Data-Source" not in client.get(path).headers


def test_openapi_operations_are_unique():
    import warnings

    # FastAPI 버전별 내부 라우터 저장 방식 대신 공개 OpenAPI로 검증한다.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        app.openapi_schema = None
        schema = app.openapi()
    operation_ids = [operation["operationId"]
                     for methods in schema["paths"].values()
                     for operation in methods.values()]
    assert len(operation_ids) == len(set(operation_ids)) == 4


def test_app_import_does_not_load_database_modules():
    import subprocess
    import sys

    script = r"""
import importlib.abc
import sys
class NoDatabaseImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'psycopg', 'psycopg2', 'sqlalchemy', 'asyncpg', 'sqlite3'}:
            raise AssertionError('DB 모듈 import 금지: ' + fullname)
sys.meta_path.insert(0, NoDatabaseImports())
from backend.app.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    for path in ['/api/v1/health', '/api/v1/parking-lots',
                 '/api/v1/parking-lots/A/spaces', '/api/v1/vehicles/10/location']:
        assert client.get(path).status_code == 200
"""
    result = subprocess.run([sys.executable, "-B", "-c", script],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_layout_matches_pm_fixture_and_independent_keys(client):
    import json
    from pathlib import Path
    layout = json.loads((Path(__file__).parent / 'fixtures/pm_parking_layout.json').read_text('utf-8'))
    keys = set()
    for lot in layout['lots']:
        body = client.get(f"/api/v1/parking-lots/{lot['lot_id']}/spaces").json()
        assert body['next_cursor'] is None
        assert {s['space_id']: s['space_type'] for s in body['items']} == {
            s['space_id']: s['space_type'] for s in lot['spaces']}
        keys.update((lot['lot_id'], s['space_id']) for s in body['items'])
        if lot['lot_id'] in 'CD':
            assert all(s['car_id'] is None and s['vehicle_type'] is None for s in body['items'])
    assert len(keys) == 104 and ('D', 'C-13') in keys and ('A', 'B-06') in keys


@pytest.mark.parametrize('car', ['01', '09', '1', '100', '17가1724'])
def test_no_legacy_alias_or_unconfirmed_plate_search(client, car):
    response = client.get(f'/api/v1/vehicles/{car}/location')
    assert response.status_code == 404 and response.json()['error']['code'] == 'VEHICLE_NOT_FOUND'
