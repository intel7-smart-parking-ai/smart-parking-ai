"""Offline safety/coverage checks, not an evaluation of model semantics."""
import json

import httpx
import pytest

from backend.app.chatbot_v3.conversation import ConversationService, Decision
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.llm import ModelError, ModelSettings, OpenRouterModel
from backend.app.chatbot_v3.place_candidates import allows_all_lots, place_candidates
from backend.app.chatbot_v3.tools import ParkingTools


@pytest.mark.parametrize('text,expected', [
    ('신세계 빈자리', ['D']), ('센텀시티는?', ['D']),
    ('기차주차장 자리 있어?', ['B']), ('부산 역 빈자리', ['B']),
    ('국제선 가까운 빈자리', ['C']), ('영화관 가까운 빈자리 추천해줘', ['D']),
    ('영화 보러 왔는데 편한 자리', ['D']), ('비행기 타러 왔어', ['C']),
    ('영화 보러 갈 건데', ['D']), ('비행기 타러 갈 건데', ['C']),
    ('수업 갈 건데', ['A']), ('쇼핑 하러 왔어', ['D']),
    ('B 주차장은?', ['B']), ('비용 알려줘', []), ('시간 알려줘', []),
    ('몰라', []), ('서울역 자리', []), ('김포공항 빈자리', []),
    ('신새계 빈자리', []), ('신새게 빈자리', []),
    ('신새계 백화점 빈자리', []), ('롯데백화점 빈자리', []),
    ('부산역 말고 공항 빈자리', ['C']),
    ('기차 주차장 말고 신세계', ['D']),
    ('공항은 아니고 부산역', ['B']), ('부산역 제외', []),
    ('부산역은 싫고 공항', ['C']), ('부산역 말고는?', []),
    ('부산역과 공항', ['B', 'C']),
    ('부산역 말고 서울역', []), ('부산역과 서울역', []),
])
def test_catalog_evidence_and_corrections(text, expected):
    assert place_candidates(text, KnowledgeStore().get_place_catalog().data, {}) == expected


@pytest.mark.parametrize('text,expected', [
    ('거기는?', ['B']), ('그 주차장 빈자리 있어?', ['B']),
    ('신새계는?', []), ('거기 말고 신새계', []),
    ('신세계 빈자리', ['D']), ('서울역', []), ('빈자리', []),
])
def test_confirmed_context_is_only_used_for_bounded_references(text, expected):
    catalog = KnowledgeStore().get_place_catalog().data
    assert place_candidates(text, catalog, {'lot_id': 'B'}) == expected
    if text in ('거기는?', '그 주차장 빈자리 있어?'):
        assert place_candidates(text, catalog, {}) == []


def decision(lot):
    return dict(intent='availability', lot_id=lot, lookup_type=None,
                vehicle_value=None, destination_id=None, space_type=None,
                preference='nearest', question=None)


@pytest.mark.parametrize('allowed,returned,valid', [
    (['B'], 'B', True), (['B'], 'D', False), ([], 'D', False),
    ([], None, True), (['B', 'C'], 'C', True),
])
def test_wire_schema_and_local_validation(allowed, returned, valid):
    def handler(request):
        params = json.loads(request.content)['tools'][0]['function']['parameters']
        branches = params['properties']['lot_id']['anyOf']
        assert branches == ([{'type': 'string', 'enum': allowed}] if allowed else []) + [{'type': 'null'}]
        assert params['additionalProperties'] is False
        assert set(params['required']) == set(params['properties'])
        return httpx.Response(200, json={
            'choices': [{'message': {'tool_calls': [{'function': {
                'name': 'submit_result', 'arguments': json.dumps(decision(returned))}}]}}],
            'usage': {'total_tokens': 12},
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OpenRouterModel(client, ModelSettings('test-key', 'test/model'))
        if valid:
            assert model.structured('prompt', {'allowed_place_ids': allowed}, Decision).lot_id == returned
        else:
            with pytest.raises(ModelError):
                model.structured('prompt', {'allowed_place_ids': allowed}, Decision)
            assert model.last_usage == {'total_tokens': 12}
    # No process-global schema mutation across requests.
    assert 'D' in Decision.model_json_schema()['properties']['lot_id']['anyOf'][0]['enum']


@pytest.mark.parametrize('text', ['신새계 빈자리', '신새게 빈자리', '부산역 빈자리'])
def test_custom_model_cannot_bypass_evidence_or_poison_context(text):
    class IgnoringModel:
        calls = 0
        def structured(self, prompt, payload, schema):
            self.calls += 1
            return Decision(**decision('D'))
    def forbidden(request):
        pytest.fail('Ungrounded place must not reach tools')
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(forbidden)) as client:
        model = IgnoringModel()
        service = ConversationService(model, ParkingTools(client), KnowledgeStore())
        result = service.handle('c', 'r', text)
        assert result['status'] == 'clarify'
        assert service._states['c']['context'] == {}
        assert service.handle('c', 'r', text) == result
        assert model.calls == 1


@pytest.mark.parametrize('text', ['신새계 주차장', '부산역 빈자리', '거기 빈자리'])
def test_null_place_cannot_broaden_unresolved_query_to_all_lots(text):
    class NullModel:
        def structured(self, prompt, payload, schema):
            return Decision(**decision(None))
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(
            lambda request: pytest.fail('Must not list all lots'))) as client:
        service = ConversationService(NullModel(), ParkingTools(client), KnowledgeStore())
        assert service.handle('c', 'r', text)['status'] == 'clarify'


def test_existing_dataset_expected_place_coverage():
    from pathlib import Path
    cases = json.loads(Path('docs/chatbot-v3/evaluation/questions.v1.json').read_text(encoding='utf-8'))['cases']
    catalog = KnowledgeStore().get_place_catalog().data
    targeted = [c for c in cases if c['expected']['slots_for_action'].get('lot_id')]
    assert len(targeted) == 11
    for case in targeted:
        assert case['expected']['slots_for_action']['lot_id'] in place_candidates(case['question'], catalog, case['context']), case['id']


def test_explicit_global_query_can_still_list_all_lots():
    from backend.app.chatbot_v3.conversation import Selection
    from backend.app.chatbot_v3.schemas import ToolResult
    class GlobalModel:
        def structured(self, prompt, payload, schema):
            return Decision(**decision(None)) if schema is Decision else Selection(fact_ids=[f['id'] for f in payload['facts']])
    class Tools:
        calls = 0
        def list_lots(self):
            self.calls += 1
            return ToolResult(status='ok', data={'items': [], 'as_of': 'offline'}, is_mock=True)
    tools = Tools()
    service = ConversationService(GlobalModel(), tools, KnowledgeStore())
    assert allows_all_lots('모든 주차장 빈자리 알려줘')
    assert not allows_all_lots('전체 주차장 중 신새계')
    assert service.handle('c', 'r', '전체 주차장 빈자리 알려줘')['status'] == 'ok'
    assert tools.calls == 1
