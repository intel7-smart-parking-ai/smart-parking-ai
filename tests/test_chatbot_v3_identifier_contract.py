"""Offline wire-contract checks; never call the paid provider."""
import json

import httpx
import pytest

from backend.app.chatbot_v3.conversation import ConversationService, Decision
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.llm import ModelError, ModelSettings, OpenRouterModel
from backend.app.chatbot_v3.tools import ParkingTools


def output(identifier, intent='vehicle'):
    return dict(intent=intent, lot_id=None, destination_id=None, space_type=None,
                preference='nearest', question=None, vehicle_identifier=identifier)


def reply(value):
    return httpx.Response(200, json={
        'choices': [{'message': {'tool_calls': [{'function': {
            'name': 'submit_result', 'arguments': json.dumps(value)}}]}}],
        'usage': {'cost': .002, 'total_tokens': 5000}})


@pytest.mark.parametrize('kind,value', [('sticker', '10'), ('sticker', '99'),
                                      ('plate_suffix', '0000'), ('plate_suffix', '0765'), ('plate_suffix', '9999')])
def test_valid_pair_is_copied_without_rewriting(kind, value):
    def handler(request):
        schema = json.loads(request.content)['tools'][0]['function']['parameters']
        assert 'lookup_type' not in schema['properties']
        assert 'vehicle_value' not in schema['properties']
        assert 'vehicle_identifier' in schema['required']
        assert schema['type'] == 'object'
        definitions = schema['$defs']
        expected_patterns = {'_StickerIdentifier': r'^[1-9][0-9]$', '_PlateSuffixIdentifier': r'^[0-9]{4}$'}
        for name, pattern in expected_patterns.items():
            obj = definitions[name]
            assert obj['additionalProperties'] is False
            assert set(obj['required']) == {'kind', 'value'}
            assert obj['properties']['value']['pattern'] == pattern
        return reply(output({'kind': kind, 'value': value}))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        d = OpenRouterModel(client, ModelSettings('test-key', 'test/model')).structured('prompt', {}, Decision)
    assert (d.lookup_type, d.vehicle_value) == (kind, value)
    assert 'vehicle_identifier' not in d.model_dump()


@pytest.mark.parametrize('identifier', [
    {'kind': 'sticker', 'value': '0765'},
    {'kind': 'sticker', 'value': '01'},
    {'kind': 'plate_suffix', 'value': '23'},
    {'kind': 'plate_suffix', 'value': '１２３４'},
    {'kind': 'plate_suffix', 'value': '１234'},
    {'kind': 'plate_suffix', 'value': '0765\n'},
    {'kind': 'plate_suffix', 'value': 765},
    {'kind': 'plate_suffix', 'value': ' 0765'},
    {'kind': 'unknown', 'value': '0765'},
    {'kind': 'plate_suffix'},
    {'kind': 'plate_suffix', 'value': '0765', 'extra': 'private'},
    None,
])
def test_invalid_pair_is_not_repaired_retried_or_sent_to_parking(identifier):
    calls = []
    def handler(request):
        calls.append(request)
        return reply(output(identifier))
    with httpx.Client(transport=httpx.MockTransport(handler)) as model_client:
        model = OpenRouterModel(model_client, ModelSettings('test-key', 'test/model'))
        with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('No parking request allowed'))) as api:
            service = ConversationService(model, ParkingTools(api), KnowledgeStore())
            result = service.handle('c', 'r', '0765 차량 어디 있어?')
            assert result['status'] == 'unavailable'
            assert result['usage'] == [{'cost': .002, 'total_tokens': 5000}]
            assert service.handle('c', 'r', '0765 차량 어디 있어?') == result
    assert len(calls) == 1


@pytest.mark.parametrize('intent', ['clarify', 'unsupported', 'availability'])
def test_non_vehicle_intent_requires_null_identifier(intent):
    with httpx.Client(transport=httpx.MockTransport(lambda _: reply(output({'kind': 'sticker', 'value': '23'}, intent)))) as client:
        with pytest.raises(ModelError):
            OpenRouterModel(client, ModelSettings('test-key', 'test/model')).structured('prompt', {}, Decision)


def test_clarification_without_identifier_remains_valid():
    args = output(None, 'clarify'); args['question'] = 'ASCII 숫자로 다시 알려주세요.'
    with httpx.Client(transport=httpx.MockTransport(lambda _: reply(args))) as client:
        d = OpenRouterModel(client, ModelSettings('test-key', 'test/model')).structured('prompt', {}, Decision)
    assert d.intent == 'clarify' and d.lookup_type is None and d.vehicle_value is None
