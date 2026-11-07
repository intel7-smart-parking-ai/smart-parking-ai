"""Offline regressions from the first live coordinator connection failure."""
import json

import httpx
import pytest

from backend.app.chatbot_v3.conversation import ConversationService, Decision, Selection
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.llm import ModelError, ModelSettings, OpenRouterModel
from backend.app.chatbot_v3.observability import TraceRecorder
from backend.app.chatbot_v3.tools import ParkingTools


def body(preference):
    return dict(intent='vehicle', lot_id=None, lookup_type='plate_suffix',
                vehicle_value='0765', destination_id=None, space_type=None,
                preference=preference, question=None)


def provider_reply(arguments):
    return httpx.Response(200, json={
        'choices': [{'message': {'tool_calls': [{'function': {
            'name': 'submit_result', 'arguments': json.dumps(arguments)}}]}}],
        'usage': {'cost': .001994, 'total_tokens': 4859, 'private': 'must-not-leak'},
    })


@pytest.mark.parametrize('preference', [None, 'closest', 0])
def test_invalid_preference_keeps_usage_without_coercion_retry_or_tools(preference):
    calls = []
    def handler(request):
        calls.append(request)
        return provider_reply(body(preference))
    recorder = TraceRecorder()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OpenRouterModel(client, ModelSettings('test-key', 'test/model'))
        with httpx.Client(transport=httpx.MockTransport(
                lambda request: pytest.fail('Invalid model output must not query parking APIs'))) as api:
            service = ConversationService(model, ParkingTools(api), KnowledgeStore(), observer=recorder)
            reply = service.handle('test-session', 'test-request', '0765 차량 어디 있어?')
            assert reply['status'] == 'unavailable'
            assert reply['usage'] == [{'cost': .001994, 'total_tokens': 4859}]
            assert service.handle('test-session', 'test-request', '0765 차량 어디 있어?') == reply
    assert len(calls) == 1
    generation = next(e for e in recorder.snapshot() if e['kind'] == 'generation')
    assert generation['metadata']['status'] == 'error'
    assert generation['usage'] == {'cost': .001994, 'total_tokens': 4859}
    assert 'must-not-leak' not in json.dumps(reply)


@pytest.mark.parametrize('schema,arguments', [(Decision, body('nearest')), (Selection, {'fact_ids': ['f0']})])
def test_valid_results_use_strict_complete_function_schema(schema, arguments):
    def handler(request):
        sent = json.loads(request.content)
        function = sent['tools'][0]['function']
        assert function['strict'] is True
        parameters = function['parameters']
        assert parameters['additionalProperties'] is False
        assert set(parameters['required']) == set(parameters['properties'])
        if schema is Decision:
            assert parameters['properties']['preference']['enum'] == ['nearest', 'easy']
        return provider_reply(arguments)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = OpenRouterModel(client, ModelSettings('test-key', 'test/model')).structured('prompt', {}, schema)
    assert result.model_dump() == arguments


def test_failed_call_does_not_reuse_previous_usage():
    replies = iter([provider_reply(body('nearest')), httpx.Response(401, json={'detail': 'private'})])
    with httpx.Client(transport=httpx.MockTransport(lambda request: next(replies))) as client:
        model = OpenRouterModel(client, ModelSettings('test-key', 'test/model'))
        model.structured('prompt', {}, Decision)
        assert model.last_usage['cost'] == .001994
        with pytest.raises(ModelError):
            model.structured('prompt', {}, Decision)
        assert model.last_usage == {}
