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
    assert sum(len(client.get(GET.format(lot)).json()['items']) for lot in 'ABCD') == 104
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
    response = client.get(GET.format('Z'))
    assert response.status_code == 404
    assert response.json() == {'error': {'code': 'PARKING_LOT_NOT_FOUND',
                                        'message': '주차장이 없습니다.', 'details': []}}


@pytest.mark.parametrize('lot,count', [('A', 12), ('B', 16), ('C', 12), ('D', 64)])
def test_common_list_and_104_layout(experiment, lot, count):
    client, store, clock = experiment
    response = client.get(GET.format(lot))
    body = response.json()
    assert response.status_code == 200 and body['next_cursor'] is None
    assert datetime.fromisoformat(body['as_of']) == clock[0]
    assert len(body['items']) == count
    spaces = {s['space_id']: s for s in body['items']}
    expected = next(x for x in LAYOUT['lots'] if x['lot_id'] == lot)
    assert {k: v['space_type'] for k, v in spaces.items()} == {
        s['space_id']: s['space_type'] for s in expected['spaces']}
    if lot == 'D':
        assert set(spaces) == {f'{zone}-{i:02}' for zone in 'ABCD' for i in range(1, 17)}
        assert {k for k, v in spaces.items() if v['space_type'] == 'ev'} == {f'D-{i:02}' for i in range(1, 9)}
        assert {k for k, v in spaces.items() if v['space_type'] == 'accessible'} == {f'C-{i:02}' for i in range(13, 17)}


@pytest.mark.parametrize('observed', ['general', 'ev', 'accessible', None])
def test_vehicle_type_comes_only_from_observation(experiment, observed):
    client, store, clock = experiment
    occupy(client, lot_id='D', camera_id=None, space_id='D-01', vehicle_type=observed)
    row = space(client, 'D', 'D-01')
    assert row['space_type'] == 'ev' and row['vehicle_type'] == observed
    assert row['status'] == 'occupied'  # 전용칸 종류로 차량 종류를 추정/차단하지 않는다.
    # 미인식 새 관측은 이전 차량 종류를 복사하지 않는다.
    client.post(POST, json=event(3, lot_id='D', camera_id=None, space_id='D-01', vehicle_type=None, confidence=.2))
    assert space(client, 'D', 'D-01')['vehicle_type'] is None


def test_vehicle_type_cleared_on_stale_and_empty(experiment):
    client, store, clock = experiment
    occupy(client, vehicle_type='ev')
    assert space(client)['vehicle_type'] == 'ev'
    clock[0] = START + timedelta(seconds=13)
    assert space(client)['vehicle_type'] is None
    clock[0] = START + timedelta(seconds=6)
    for tick in (3, 4, 5, 6):
        client.post(POST, json=event(tick, event_type='space_empty_observed', car_id=None, vehicle_type=None))
    assert space(client)['status'] == 'empty' and space(client)['vehicle_type'] is None


def test_pending_vehicle_type_and_d_zone_independence(experiment):
    client, store, clock = experiment
    client.post(POST, json=event(0, vehicle_type='ev', lot_id='D', camera_id=None, space_id='C-13'))
    assert space(client, 'D', 'C-13')['vehicle_type'] is None
    for tick in (1, 2):
        client.post(POST, json=event(tick, vehicle_type='ev', lot_id='D', camera_id=None, space_id='C-13'))
    assert space(client, 'D', 'C-13')['vehicle_type'] == 'ev'
    assert space(client, 'C', 'A-01')['status'] == 'empty'


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
