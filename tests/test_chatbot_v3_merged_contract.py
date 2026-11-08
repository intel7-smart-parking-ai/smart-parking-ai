"""Integration regressions for the two recovered histories, entirely offline."""
import json
import httpx
import pytest
from backend.app.chatbot_v3.conversation import ConversationService, Decision
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.llm import ModelError, ModelSettings, OpenRouterModel
from backend.app.chatbot_v3.place_candidates import place_candidates
from tests.test_chatbot_v3_request_boundaries import Model, decision


@pytest.mark.parametrize('text,lot,context', [
    ('신 세 계 주차장 안내', 'D', {}),
    ('그곳 빈자리 좀 확인해줘', 'B', {'lot_id': 'B'}),
    ('그 주차장 빈자리 있어?', 'B', {'lot_id': 'B'}),
    ('이 주차장 안내', 'C', {'lot_id': 'C'}),
    ('영화 보러 갈 건데', 'D', {}),
    ('부산역 말고 공항 빈자리', 'C', {'lot_id': 'B'}),
    ('서울역 말고 부산역 빈자리', 'B', {}),
])
def test_schema_candidates_and_execution_boundary_agree(text, lot, context):
    service = ConversationService(Model(decision('guidance', lot)), object(), KnowledgeStore())
    assert lot in place_candidates(text, service.catalog, context)
    service._validate(decision('guidance', lot), text, context)


@pytest.mark.parametrize('text,lot,context', [
    ('신새계 쪽 주차장 안내해줘', 'D', {}),
    ('신새게 주차장 빈자리 알려줘', 'D', {'lot_id': 'D'}),
    ('영화관 가까운 자리', 'D', {'lot_id': 'B'}),
    ('부산역 말고 공항 빈자리', 'B', {'lot_id': 'B'}),
])
def test_unsupported_selection_rejected_at_both_boundaries(text, lot, context):
    service = ConversationService(Model(decision('guidance', lot)), object(), KnowledgeStore())
    assert lot not in place_candidates(text, service.catalog, context)
    with pytest.raises(ValueError):
        service._validate(decision('guidance', lot), text, context)


@pytest.mark.parametrize('text', ['전체 주차장 빈자리', '모든 주차장 현황', '각 주차장 자리',
                                '주차장 전체 빈자리', '주차장 모두 현황', '주차장별 빈자리'])
def test_restored_global_phrases_through_service(text):
    from backend.app.chatbot_v3.schemas import ToolResult
    class Tools:
        def list_lots(self):
            return ToolResult(status='ok', data={'items': [], 'as_of': 'offline'}, is_mock=True)
    service = ConversationService(Model(decision()), Tools(), KnowledgeStore())
    assert service.handle('c', 'r', text)['status'] == 'ok'


@pytest.mark.parametrize('kind,lot,valid', [('plate_suffix', 'B', True),
                                         ('sticker', 'B', False),
                                         ('plate_suffix', 'D', False)])
def test_typed_identifier_and_request_enum_are_enforced_together(kind, lot, valid):
    def handler(request):
        schema = json.loads(request.content)['tools'][0]['function']['parameters']
        assert schema['properties']['lot_id']['anyOf'][0]['enum'] == ['B']
        assert schema['$defs']['_PlateSuffixIdentifier']['properties']['value']['pattern'] == r'^[0-9]{4}$'
        args = dict(intent='vehicle', lot_id=lot, destination_id=None, space_type=None,
                    preference='nearest', question=None, vehicle_identifier={'kind': kind, 'value': '0765'})
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [{'function': {
            'name': 'submit_result', 'arguments': json.dumps(args)}}]}}], 'usage': {'total_tokens': 7}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OpenRouterModel(client, ModelSettings('test-key', 'test/model'))
        if valid:
            result = model.structured('prompt', {'allowed_place_ids': ['B']}, Decision)
            assert (result.lookup_type, result.vehicle_value) == ('plate_suffix', '0765')
        else:
            with pytest.raises(ModelError):
                model.structured('prompt', {'allowed_place_ids': ['B']}, Decision)
            assert model.last_usage == {'total_tokens': 7}


def test_ui_timeout_signal_stops_future_work_even_before_clock_deadline():
    import time
    from threading import Event
    from backend.app.chatbot_v3.budget import BudgetExpired, remaining_timeout, turn_budget
    cancelled = Event()
    with turn_budget(time.monotonic() + 60, cancelled):
        assert remaining_timeout(1) == 1
        cancelled.set()
        with pytest.raises(BudgetExpired):
            remaining_timeout(1)
    assert remaining_timeout(1) == 1
