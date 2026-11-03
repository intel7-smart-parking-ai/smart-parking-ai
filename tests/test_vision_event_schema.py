"""DB·카메라 없는 JSON fixture/입력 경계 검증. 운영 판정 시험은 아니다."""
import json
from datetime import datetime
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from backend.app.api.vision_event_schema import VisionEvent

FIXTURE = Path(__file__).parent / 'fixtures' / 'vision_event_sequences.json'


def scenarios():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def example():
    return scenarios()['normal'][0]


@pytest.mark.parametrize('name', ['normal','temporary_miss','empty_space','unrecognized','move','low_confidence','duplicate'])
def test_fixture_sequences(name):
    sequence = scenarios()[name]
    parsed = [VisionEvent.model_validate(event) for event in sequence]
    assert len(parsed) >= 1
    assert [event.observed_at for event in parsed] == sorted(event.observed_at for event in parsed)
    assert [json.loads(event.model_dump_json())['car_id'] for event in parsed] == [event['car_id'] for event in sequence]


def test_samples_are_evidence_without_state_decisions():
    data = scenarios()
    assert set(data) == {'normal','temporary_miss','empty_space','unrecognized','move','low_confidence','duplicate'}
    assert data['duplicate'][0] == data['duplicate'][1]
    assert len({event['event_id'] for event in data['move']}) == 2
    assert data['move'][0]['space_id'] != data['move'][1]['space_id']
    assert data['unrecognized'][0]['car_id'] is None
    assert data['low_confidence'][0]['confidence'] == .2
    assert data['empty_space'][0]['event_type'] == 'space_empty_observed'
    assert len(data['temporary_miss']) == 2  # 중간 무관측은 이벤트가 아니며 출차 결론도 없다.


@pytest.mark.parametrize('field,value', [
    ('event_id',' '),('event_id',1),('event_type','entry_confirmed'),('source','unknown'),
    ('lot_id','Z'),('camera_id',''),('space_id',1),('car_id',1),('car_id','1'),('car_id','00'),('car_id','09'),
    ('confidence',-.1),('confidence',1.1),('confidence',float('nan')),('confidence',float('inf')),('confidence',True),('confidence','0.9'),
    ('bbox_xyxy',[1,2,3]),('bbox_xyxy',[3,2,1,4]),('bbox_xyxy',[-1,0,1,2]),('bbox_xyxy',[0,0,float('inf'),2]),
    ('observed_at','2026-11-03T10:00:00'),('observed_at',1234),('observed_at','bad-time')])
def test_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        VisionEvent.model_validate(example() | {field:value})


@pytest.mark.parametrize('source', ['vision','simulation','manual'])
def test_draft_sources_and_nullable_observation(source):
    value = example() | {'source':source,'car_id':None,'space_id':None,'bbox_xyxy':None}
    assert VisionEvent.model_validate(value).source == source


@pytest.mark.parametrize('timestamp', ['2026-11-03T10:00:00+09:00','2026-11-03T01:00:00Z'])
def test_timezone_offsets(timestamp):
    assert VisionEvent.model_validate(example() | {'observed_at':timestamp}).observed_at.utcoffset() is not None


@pytest.mark.parametrize('kind', ['vehicle_observed','space_empty_observed','plate_observed','mock_fire_led_observed'])
def test_contract_event_types(kind):
    assert VisionEvent.model_validate(example() | {'event_type':kind}).event_type == kind


def test_naive_datetime_object_and_equivalent_instants():
    with pytest.raises(ValidationError):
        VisionEvent.model_validate(example() | {'observed_at':datetime(2026,11,6,10)})
    first = VisionEvent.model_validate(example() | {'observed_at':'2026-11-06T10:00:00+09:00'})
    second = VisionEvent.model_validate(example() | {'observed_at':'2026-11-06T01:00:00Z'})
    assert first.observed_at == second.observed_at


def test_required_fields_and_canonical_bbox_name():
    value = example()
    for key in value:
        if key == 'vehicle_type':  # 불확실/이전 생산자의 생략은 null로 유지한다.
            continue
        incomplete = value.copy()
        del incomplete[key]
        with pytest.raises(ValidationError):
            VisionEvent.model_validate(incomplete)
    with pytest.raises(ValidationError):
        VisionEvent.model_validate(value | {'bbox':[1,2,3,4]})
    with pytest.raises(ValidationError):
        VisionEvent.model_validate(value | {'lot_id':'C'})
    assert VisionEvent.model_validate(value | {'lot_id':'C','camera_id':None}).camera_id is None


def test_duplicate_is_schema_valid_but_not_processed():
    first, second = [VisionEvent.model_validate(v) for v in scenarios()['duplicate']]
    assert first == second  # 영속 저장·해시 비교·중복 제거는 아직 수행하지 않는다.


def test_fresh_import_without_database_or_camera():
    code = '''
import importlib.abc,sys,socket
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sqlite3','sqlalchemy','psycopg','psycopg2','cv2','alembic'}:
            raise AssertionError('DB/카메라 import 금지')
sys.meta_path.insert(0,Block())
from backend.app.api.vision_event_schema import VisionEvent
assert 'source' in VisionEvent.model_fields
'''
    result = subprocess.run([sys.executable,'-B','-X','utf8','-c',code],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
