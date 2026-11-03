"""11/03 PM 규격의 입력 및 도면 fixture 시험. DB 시드/운영 정책은 만들지 않는다."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.vision_event_receiver import create_experiment_app
from backend.app.api.vision_event_store import InMemoryEventStore

FIXTURES = Path(__file__).parent / 'fixtures'
LAYOUT = json.loads((FIXTURES / 'pm_parking_layout.json').read_text('utf-8'))
SEQUENCES = json.loads((FIXTURES / 'vision_event_sequences.json').read_text('utf-8'))
PATH = '/api/v1/internal/vision-events'


@pytest.mark.parametrize('field,value,code', [
    ('car_id', '10', 202), ('car_id', '99', 202), ('car_id', None, 202),
    ('car_id', '09', 422), ('car_id', 10, 422), ('car_id', 99, 422),
    ('car_id', '00', 422), ('car_id', '100', 422), ('car_id', '17가1724', 422),
    ('vehicle_type', 'general', 202), ('vehicle_type', 'ev', 202),
    ('vehicle_type', 'accessible', 202), ('vehicle_type', None, 202),
    ('vehicle_type', 'unknown', 422), ('vehicle_type', 1, 422),
])
def test_pm_input_rules(field, value, code):
    store = InMemoryEventStore()
    with TestClient(create_experiment_app(store=store, authorize=lambda: True)) as client:
        response = client.post(PATH, json=SEQUENCES['normal'][0] | {field: value})
        assert response.status_code == code
    assert len(store) == (1 if code == 202 else 0)


@pytest.mark.parametrize('lot,name,per_zone,zones,ev,accessible', [
    ('A', '개발원주차장', 6, 'AB', {'A-05', 'A-06'}, {'B-06'}),
    ('B', '부산역주차장', 8, 'AB', {'A-07', 'A-08'}, {'B-07', 'B-08'}),
    ('C', '북항주차장', 6, 'AB', {'A-05', 'A-06'}, {'B-06'}),
    ('D', '신세계백화점주차장', 16, 'ABCD',
     {f'D-{i:02}' for i in range(1, 9)}, {f'C-{i:02}' for i in range(13, 17)}),
])
def test_pm_lot_layout(lot, name, per_zone, zones, ev, accessible):
    item, = [item for item in LAYOUT['lots'] if item['lot_id'] == lot]
    assert item['name'] == name
    spaces = item['spaces']
    assert len(spaces) == per_zone * len(zones)
    assert {s['space_id'] for s in spaces} == {
        f'{zone}-{i:02}' for zone in zones for i in range(1, per_zone + 1)}
    assert {s['space_id'] for s in spaces if s['space_type'] == 'ev'} == ev
    assert {s['space_id'] for s in spaces if s['space_type'] == 'accessible'} == accessible
    assert {s['space_id'] for s in spaces if s['space_type'] == 'general'} == {
        s['space_id'] for s in spaces} - ev - accessible


def test_104_composite_keys_and_virtual_plate_example():
    assert [lot['lot_id'] for lot in LAYOUT['lots']] == ['A', 'B', 'C', 'D']
    keys = [(lot['lot_id'], s['space_id']) for lot in LAYOUT['lots'] for s in lot['spaces']]
    assert len(keys) == len(set(keys)) == 104
    assert sum(lot in ('A', 'B') for lot, space in keys) == 28
    assert sum(lot in ('C', 'D') for lot, space in keys) == 76
    assert sum(space == 'A-01' for lot, space in keys) == 4
    assert ('A', 'B-06') in keys and ('B', 'A-08') in keys
    assert LAYOUT['virtual_plate_example']['plate_number'] == '17가1724'
    assert LAYOUT['virtual_plate_example']['display'] == '1724'


def test_sample_fields_and_layout_agree():
    keys = {(lot['lot_id'], s['space_id']) for lot in LAYOUT['lots'] for s in lot['spaces']}
    assert len(SEQUENCES) == 7
    for seq in SEQUENCES.values():
        for event in seq:
            assert event['car_id'] in ('10', None)
            assert event['vehicle_type'] in ('general', None)
            assert (event['lot_id'], event['space_id']) in keys


@pytest.mark.parametrize('source', ['simulation', 'manual'])
def test_simulated_sources_remain_distinct(source):
    from backend.app.api.vision_event_store import canonical_payload
    from backend.app.api.vision_event_schema import VisionEvent
    event = VisionEvent.model_validate(SEQUENCES['normal'][0] | {'source': source})
    assert json.loads(canonical_payload(event))['source'] == source


def test_vehicle_type_participates_in_idempotency():
    store = InMemoryEventStore()
    value = SEQUENCES['normal'][0]
    with TestClient(create_experiment_app(store=store, authorize=lambda: True)) as client:
        assert client.post(PATH, json=value).status_code == 202
        assert client.post(PATH, json=value).status_code == 200
        assert client.post(PATH, json=value | {'vehicle_type': 'ev'}).status_code == 409
    assert len(store) == 1
