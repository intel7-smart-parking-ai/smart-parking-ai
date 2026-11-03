"""D5 합성 관측→수신→메모리 상태→조회 시험. 세션/실물 주차 시험이 아니다."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.occupancy_experiment_app import create_occupancy_app
from backend.app.api.occupancy_prototype import ExperimentPolicy, OccupancyExperiment

FIXTURES = Path(__file__).parent / 'fixtures'
LAYOUT = json.loads((FIXTURES / 'pm_parking_layout.json').read_text('utf-8'))
SAMPLE = json.loads((FIXTURES / 'vision_event_sequences.json').read_text('utf-8'))['normal'][0]
START = datetime.fromisoformat(SAMPLE['observed_at'])
POST = '/api/v1/internal/vision-events'
GET = '/api/v1/parking-lots/{}/spaces'


@pytest.fixture
def experiment():
    # 공식 정책이 아닌 빠른 합성 시연용 가정. 첫 empty도 도면의 실제 관측값이 아니다.
    policy = ExperimentPolicy(occupy_seconds=2, empty_seconds=3, max_gap_seconds=2,
                              stale_seconds=10, min_confidence=.8, initial_status='empty')
    store = OccupancyExperiment(layout=LAYOUT, policy=policy)
    clock = [START]
    with TestClient(create_occupancy_app(store=store, authorize=lambda: True, clock=lambda: clock[0])) as client:
        yield client, store, clock


def event(tick, **changes):
    return SAMPLE | {'event_id': f'd5-{tick}', 'source': 'simulation',
                     'observed_at': (START + timedelta(seconds=tick)).isoformat()} | changes


def space(client, lot='A', space_id='A-01'):
    return next(s for s in client.get(GET.format(lot)).json()['items'] if s['space_id'] == space_id)


def occupy(client, **changes):
    for tick in (0, 1, 2):
        assert client.post(POST, json=event(tick, **changes)).status_code == 202


def test_empty_occupied_empty_demo(experiment):
    client, store, clock = experiment
    assert space(client)['status'] == 'empty'
    occupy(client)
    assert space(client)['status'] == 'occupied' and space(client)['car_id'] == '10'
    for tick in (3, 4, 5, 6):
        response = client.post(POST, json=event(tick, event_type='space_empty_observed', car_id=None, bbox_xyxy=None))
        assert response.status_code == 202
        assert space(client)['status'] == ('empty' if tick == 6 else 'uncertain')
    assert space(client)['car_id'] is None


def test_duplicate_conflict_do_not_change_state(experiment):
    client, store, clock = experiment
    occupy(client)
    before = space(client)
    assert client.post(POST, json=event(2)).status_code == 200
    assert client.post(POST, json=event(2, confidence=.1)).status_code == 409
    assert space(client) == before


def test_same_space_name_is_independent_per_lot(experiment):
    client, store, clock = experiment
    occupy(client, lot_id='C', camera_id=None)
    assert space(client, 'C')['status'] == 'occupied'
    assert space(client, 'A')['status'] == 'empty'
    assert sum(len(client.get(GET.format(lot)).json()['items']) for lot in 'ABCD') == 50
    assert space(client, 'B', 'B-08')['space_type'] == 'accessible'


@pytest.mark.parametrize('source,is_mock', [('simulation', True), ('manual', True), ('vision', False)])
def test_source_and_mock_markers(experiment, source, is_mock):
    client, store, clock = experiment
    occupy(client, source=source)
    row = space(client)
    assert row['source'] == source and row['is_mock'] is is_mock
    assert client.get(GET.format('A')).json()['data_source'] == 'memory-prototype'


def test_low_confidence_and_missing_plate(experiment):
    client, store, clock = experiment
    occupy(client, car_id=None)  # 번호 미인식은 점유 미확인과 별개다.
    assert space(client)['status'] == 'occupied' and space(client)['car_id'] is None
    client.post(POST, json=event(3, confidence=.2))
    assert space(client)['status'] == 'uncertain'


def test_gap_resets_confirmation_and_stale_is_not_exit(experiment):
    client, store, clock = experiment
    client.post(POST, json=event(0))
    client.post(POST, json=event(4))
    assert space(client)['status'] == 'uncertain'
    for tick in (5, 6):
        client.post(POST, json=event(tick))
    assert space(client)['status'] == 'occupied'
    clock[0] = START + timedelta(seconds=17)
    assert space(client)['status'] == 'uncertain'  # 마지막 관측 이후10초 초과: empty가 아니다.


def test_late_or_same_time_observation_cannot_overwrite(experiment):
    client, store, clock = experiment
    occupy(client)
    before = space(client)
    for tick in (1, 2):
        assert client.post(POST, json=event(tick, event_id=f'late-{tick}',
            event_type='space_empty_observed', car_id=None)).status_code == 202
    assert space(client) == before


def test_concurrent_duplicate_is_applied_once(experiment):
    client, store, clock = experiment
    with ThreadPoolExecutor(20) as pool:
        codes = list(pool.map(lambda _: client.post(POST, json=event(0)).status_code, range(20)))
    assert codes.count(202) == 1 and codes.count(200) == 19
    assert space(client)['version'] == 1 and space(client)['status'] == 'uncertain'


def test_unknown_space_and_lot(experiment):
    client, store, clock = experiment
    assert client.post(POST, json=event(0, space_id='A-99')).status_code == 422
    assert len(store) == 0
    assert client.get(GET.format('Z')).status_code == 404


def test_plate_and_led_are_not_occupancy_evidence(experiment):
    client, store, clock = experiment
    before = space(client)
    for tick, kind in enumerate(('plate_observed', 'mock_fire_led_observed')):
        assert client.post(POST, json=event(tick, event_type=kind)).status_code == 202
    assert space(client) == before


def test_read_and_write_authorization_required(experiment):
    _, store, clock = experiment
    with TestClient(create_occupancy_app(store=store, authorize=lambda: False, clock=lambda: clock[0])) as client:
        assert client.post(POST, json=event(0)).status_code == 403
        assert client.get(GET.format('A')).status_code == 403
    assert len(store) == 0


@pytest.mark.parametrize('field,value', [('occupy_seconds', 0), ('empty_seconds', -1),
    ('max_gap_seconds', float('inf')), ('stale_seconds', True), ('min_confidence', 1.1)])
def test_invalid_experiment_policy(field, value):
    values = dict(occupy_seconds=2, empty_seconds=3, max_gap_seconds=2,
                  stale_seconds=10, min_confidence=.8, initial_status='empty')
    with pytest.raises(ValueError):
        ExperimentPolicy(**(values | {field: value}))
