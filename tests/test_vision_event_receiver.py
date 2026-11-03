"""DB 없이 실행하는 수신·중복·동시 요청 실험. 운영 인증/영속성을 검증하지 않는다."""

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend.app.api.vision_event_receiver import create_experiment_app, create_router
from backend.app.api.vision_event_schema import VisionEvent
from backend.app.api.vision_event_store import InMemoryEventStore, Receipt

PATH = "/api/v1/internal/vision-events"
HEADERS = {"X-Test-Caller": "fixture"}  # 시험 호출 구분자이며 운영용 비밀키/인증이 아니다.
SAMPLES = json.loads((Path(__file__).parent / "fixtures/vision_event_sequences.json").read_text("utf-8"))


def authorize_fixture(request: Request):
    if request.headers.get("X-Test-Caller") != "fixture":
        raise HTTPException(401, "시험 호출만 허용", headers={"WWW-Authenticate": "Test-only"})
    return True


@pytest.fixture
def receiver():
    store = InMemoryEventStore()
    with TestClient(create_experiment_app(store=store, authorize=authorize_fixture)) as client:
        yield client, store


def example():
    return SAMPLES["normal"][0].copy()


def post(client, payload):
    return client.post(PATH, json=payload, headers=HEADERS)


@pytest.mark.parametrize("name", list(SAMPLES))
def test_seven_sequences(receiver, name):
    client, store = receiver
    responses = [post(client, item) for item in SAMPLES[name]]
    assert [r.status_code for r in responses] == ([202, 200] if name == "duplicate" else [202] * len(responses))
    assert len(store) == len({item["event_id"] for item in SAMPLES[name]})
    assert all(set(r.json()) == {"event_id", "disposition"} for r in responses)


def test_retry_and_conflict_preserve_first_receipt(receiver):
    client, store = receiver
    value = example()
    assert post(client, value).json() == {"event_id": value["event_id"], "disposition": "accepted"}
    response = post(client, value | {"confidence": .1})
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "EVENT_ID_CONFLICT", "message": "같은 event_id의 내용이 다릅니다.", "details": []}}
    assert post(client, value).json()["disposition"] == "duplicate"
    assert len(store) == 1


@pytest.mark.parametrize("field,value", [
    ("car_id", 1), ("car_id", "1"), ("car_id", "01"),
    ("observed_at", "2026-11-06T10:00:00"), ("confidence", 1.2),
    ("source", "not-a-source"), ("event_type", "entry_confirmed"),
    ("bbox_xyxy", [4, 0, 1, 2]), ("event_id", ""),
])
def test_invalid_body_is_422_without_receipt(receiver, field, value):
    client, store = receiver
    response = post(client, example() | {field: value})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR" and error["details"]
    assert "input" not in error["details"][0] and "ctx" not in error["details"][0]
    assert len(store) == 0


@pytest.mark.parametrize("payload", [{}, [], None, {"unknown": "value"}])
def test_missing_or_wrong_body(receiver, payload):
    client, store = receiver
    assert post(client, payload).status_code == 422
    assert len(store) == 0


def test_malformed_json(receiver):
    client, store = receiver
    response = client.post(PATH, content="{broken", headers=HEADERS | {"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(store) == 0


def test_unauthorized_call_cannot_store(receiver):
    client, store = receiver
    for headers in ({}, {"X-Test-Caller": "wrong"}):
        response = client.post(PATH, json=example(), headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
        assert response.headers["WWW-Authenticate"] == "Test-only"
    assert len(store) == 0


def test_explicit_denial_and_no_default_authorizer():
    def deny():
        raise HTTPException(403, "거절")
    store = InMemoryEventStore()
    with TestClient(create_experiment_app(store=store, authorize=deny)) as client:
        assert post(client, example()).status_code == 403
    assert len(store) == 0
    with pytest.raises(TypeError):
        create_experiment_app(store=store)
    with pytest.raises(ValueError):
        create_router(store=store, authorize=None)


@pytest.mark.parametrize("decision", [False, None, 1, "true"])
def test_only_explicit_true_authorizes(decision):
    def authorize():
        return decision
    store = InMemoryEventStore()
    with TestClient(create_experiment_app(store=store, authorize=authorize)) as client:
        assert post(client, example()).status_code == 403
    assert len(store) == 0


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"])
def test_extreme_aware_timestamp(receiver, timestamp):
    client, store = receiver
    value = example() | {"observed_at": timestamp}
    assert post(client, value).status_code == 202
    assert post(client, value).status_code == 200
    assert len(store) == 1


def test_equivalent_validated_json_is_duplicate(receiver):
    client, store = receiver
    value = example()
    assert post(client, value).status_code == 202
    same = dict(reversed(list(value.items())))
    same["observed_at"] = "2026-11-06T01:00:00Z"
    same["bbox_xyxy"] = [float(n) for n in same["bbox_xyxy"]]
    same["frame_id"] = None
    assert post(client, same).status_code == 200
    assert len(store) == 1


@pytest.mark.parametrize("field,value", [
    ("source", "manual"), ("car_id", None), ("space_id", "A-02"),
    ("observed_at", "2026-11-06T10:00:01+09:00"), ("frame_id", "frame-new"),
])
def test_changed_valid_content_is_conflict(receiver, field, value):
    client, store = receiver
    assert post(client, example()).status_code == 202
    assert post(client, example() | {field: value}).status_code == 409
    assert len(store) == 1


def concurrent_posts(client, payloads):
    barrier = Barrier(len(payloads))

    def call(payload):
        barrier.wait(timeout=15)
        return post(client, payload)

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(call, payloads))


@pytest.mark.parametrize("mode", ["identical", "conflicting", "distinct"])
def test_concurrent_api_requests(receiver, mode):
    client, store = receiver
    original = example()
    payloads = [original.copy() for _ in range(20)]
    if mode == "conflicting":
        payloads[10:] = [original | {"confidence": .1} for _ in range(10)]
    if mode == "distinct":
        payloads = [original | {"event_id": f"concurrent-{i}"} for i in range(20)]
    responses = concurrent_posts(client, payloads)
    codes = [r.status_code for r in responses]
    expected = {"identical": {202: 1, 200: 19}, "conflicting": {202: 1, 200: 9, 409: 10},
                "distinct": {202: 20}}[mode]
    assert {code: codes.count(code) for code in set(codes)} == expected
    assert len(store) == (20 if mode == "distinct" else 1)
    for response in responses:
        if response.status_code == 409:
            assert response.json()["error"]["code"] == "EVENT_ID_CONFLICT"


def test_capacity_does_not_forget_existing_ids():
    store = InMemoryEventStore(max_events=1)
    with TestClient(create_experiment_app(store=store, authorize=authorize_fixture)) as client:
        assert post(client, example()).status_code == 202
        full = post(client, example() | {"event_id": "another"})
        assert full.status_code == 503
        assert full.json()["error"]["code"] == "EXPERIMENT_STORE_FULL"
        assert post(client, example()).status_code == 200
        assert post(client, example() | {"confidence": .1}).status_code == 409
    assert len(store) == 1


def test_concurrent_capacity_bound():
    store = InMemoryEventStore(max_events=1)
    with TestClient(create_experiment_app(store=store, authorize=authorize_fixture)) as client:
        responses = concurrent_posts(client, [example() | {"event_id": str(i)} for i in range(20)])
    assert sorted(r.status_code for r in responses) == [202] + [503] * 19
    assert len(store) == 1


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_capacity(limit):
    with pytest.raises(ValueError):
        InMemoryEventStore(max_events=limit)


def test_new_store_loses_receipts_explicitly():
    event = VisionEvent.model_validate(example())
    first, restarted = InMemoryEventStore(), InMemoryEventStore()
    assert first.receive(event) is Receipt.ACCEPTED
    assert first.receive(event) is Receipt.DUPLICATE
    assert restarted.receive(event) is Receipt.ACCEPTED  # 재시작/다중프로세스 보장 아님.


def test_operational_app_is_unchanged_and_no_global_experiment_app():
    from backend.app.main import app
    with TestClient(app) as client:
        assert client.post(PATH, json=example()).status_code == 404
        assert client.get("/api/v1/health").status_code == 200
        assert PATH not in client.get("/openapi.json").json()["paths"]
    module = importlib.import_module("backend.app.api.vision_event_receiver")
    assert not hasattr(module, "app") and not hasattr(module, "router")
