"""Injected decisions from live run 1; these tests do not measure LLM accuracy."""
import httpx
import pytest

from backend.app.chatbot_v3.conversation import ConversationService, Decision, Selection
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.tools import ParkingTools


def decision(**overrides):
    return Decision(**(dict(intent='availability', lot_id='B', lookup_type=None,
                           vehicle_value=None, destination_id=None, space_type=None,
                           preference='nearest', question=None) | overrides))


class InjectedModel:
    last_usage = {}

    def __init__(self, selected):
        self.selected = selected
        self.calls = 0

    def structured(self, system, payload, schema):
        self.calls += 1
        if schema is Decision:
            return self.selected
        return Selection(fact_ids=[f['id'] for f in payload['facts']])


# Selected observed failures, not corrected or silently coerced decisions.
@pytest.mark.parametrize('text,selected', [
    ('신세계에 영화 보러 왔는데 가까운 빈자리 추천해줘',
     decision(intent='recommend', lot_id='D', space_type='general', question='영화관 근처 추천해줘')),
    ('엘리베이터 옆에 세우고 싶어',
     decision(intent='recommend', lot_id=None, space_type='general', preference='easy', question='추천해줘')),
    ('영화관 가까운 자리',
     decision(intent='recommend', lot_id='B', question='추천 가능한가요?')),
    ('부산역과 공항 중 어디가 더 비었어?',
     decision(lot_id=None, question='어느 곳이 더 빈자리가 많은가요?')),
    ('0765 차량 어디 있어?',
     decision(intent='vehicle', lot_id=None, lookup_type='sticker', vehicle_value='0765')),
    ('0765 차량 요금 알려줘',
     decision(intent='fee', lot_id=None, lookup_type='sticker', vehicle_value='0765')),
    ('01번 미니카 어디?',
     decision(intent='vehicle', lot_id=None, lookup_type='sticker', vehicle_value='01', question='위치 알려줘')),
    ('１２３４ 차량 위치',
     decision(intent='vehicle', lot_id=None, lookup_type='plate_suffix', vehicle_value='1234')),
    ('신세계 영화관 가까우면서 주차 편한 일반칸 추천',
     decision(intent='recommend', lot_id='D', space_type='general', preference='easy', question='추천')),
])
def test_observed_bad_decisions_never_execute_or_poison_context(text, selected):
    def no_api(request):
        pytest.fail('Invalid decision must not query APIs')
    model = InjectedModel(selected)
    with httpx.Client(transport=httpx.MockTransport(no_api)) as client:
        service = ConversationService(model, ParkingTools(client), KnowledgeStore())
        first = service.handle('c', 'r', text)
        assert first['status'] == 'clarify'
        assert service._states['c']['context'] == {}
        assert service.handle('c', 'r', text) == first
    assert model.calls == 1


@pytest.mark.parametrize('selected,text', [
    (decision(intent='vehicle', lot_id=None, lookup_type='sticker', vehicle_value='23'), '23번 차 어디?'),
    (decision(intent='vehicle', lot_id=None, lookup_type='plate_suffix', vehicle_value='0765'), '0765 차량 어디 있어?'),
    (decision(intent='fee', lot_id=None, lookup_type='plate_suffix', vehicle_value='0765'), '0765 차량 요금 알려줘'),
    (decision(intent='recommend', lot_id='D', destination_id='D_cinema', preference='easy'), '신세계 영화관 가까우면서 주차 편한 일반칸 추천'),
    (decision(lot_id=None), '전체 주차장 빈자리 현황'),
])
def test_valid_decision_arguments_remain_supported(selected, text):
    with httpx.Client() as client:
        service = ConversationService(InjectedModel(selected), ParkingTools(client), KnowledgeStore())
        service._validate(selected, text, {})


@pytest.mark.parametrize('text', ['신세계 주차장은 지하야?', '공항 주차장 높이 제한 몇 미터야?'])
def test_guidance_uses_local_scenario_and_does_not_invent_height(text):
    selected = decision(intent='guidance', lot_id='D' if text.startswith('신세계') else 'C')
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('Guidance is local knowledge'))) as client:
        service = ConversationService(InjectedModel(selected), ParkingTools(client), KnowledgeStore())
        result = service.handle('c', 'r', text)
    assert result['status'] == 'ok'
    assert result['is_mock'] is True
    assert '가상' in result['text']
    if selected.lot_id == 'C':
        assert '미터' not in result['text']


@pytest.mark.parametrize('text,value', [('１２３４ 차량 위치', '0765'), ('１23 차량 어디?', '23')])
def test_unicode_digits_cannot_silently_reuse_previous_ascii_vehicle(text, value):
    selected = decision(intent='vehicle', lot_id=None,
                        lookup_type='plate_suffix' if len(value) == 4 else 'sticker', vehicle_value=value)
    with httpx.Client() as client:
        service = ConversationService(InjectedModel(selected), ParkingTools(client), KnowledgeStore())
        context = {'lookup_type': selected.lookup_type, 'vehicle_value': value}
        with pytest.raises(ValueError):
            service._validate(selected, text, context)


@pytest.mark.parametrize('value', ['23', '0765'])
def test_pronoun_can_still_reuse_confirmed_ascii_vehicle(value):
    selected = decision(intent='fee', lot_id=None,
                        lookup_type='plate_suffix' if len(value) == 4 else 'sticker', vehicle_value=value)
    with httpx.Client() as client:
        service = ConversationService(InjectedModel(selected), ParkingTools(client), KnowledgeStore())
        service._validate(selected, '그 차 요금은?', {'lookup_type': selected.lookup_type, 'vehicle_value': value})
