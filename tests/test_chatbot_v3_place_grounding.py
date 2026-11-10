"""Offline selection evidence tests; no paid model accuracy claims."""
import pytest

from backend.app.chatbot_v3.conversation import ConversationService, Decision
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.place_grounding import place_evidence


@pytest.fixture
def catalog():
    return KnowledgeStore().get_place_catalog().data


@pytest.mark.parametrize('lot,text,context,source', [
    ('D', '신세계 주차장 안내', {}, 'catalog_name'),
    ('D', '신 세 계 주차장 안내', {}, 'catalog_name'),
    ('B', 'ktx 주차장 빈자리', {}, 'catalog_name'),
    ('B', '역 주차장 남은 자리', {}, 'catalog_related'),
    ('B', '기차주차장 자리', {}, 'catalog_name'),
    ('C', '국제선 가까운 자리', {}, 'catalog_related'),
    ('D', '영화관 가까운 자리', {}, 'catalog_related'),
    ('A', '교육 참석 주차 안내', {}, 'catalog_related'),
    ('D', '영화 보러 왔는데 편한 자리', {}, 'catalog_related'),
    ('B', '거기 빈자리', {'lot_id': 'B'}, 'confirmed_context'),
    ('D', '그곳 주차장 안내', {'lot_id': 'D'}, 'confirmed_context'),
    ('C', '부산역 말고 공항 빈자리', {'lot_id': 'B'}, 'catalog_name'),
])
def test_registered_phrases_and_confirmed_references(catalog, lot, text, context, source):
    evidence = place_evidence(catalog, lot, text, context)
    assert evidence is not None and evidence.source == source


@pytest.mark.parametrize('lot,text,context', [
    ('D', '신새계 쪽 주차장 안내해줘', {}),
    ('D', '신세개 주차장 빈자리', {}),
    ('D', '신새계 쪽 주차장 안내해줘', {'lot_id': 'D'}),
    ('B', '거기 빈자리', {}),
    ('D', '거기 빈자리', {'lot_id': 'B'}),
    ('B', '신세계 주차장 안내', {'lot_id': 'B'}),
    ('D', '영화관 가까운 자리', {'lot_id': 'B'}),
    ('B', '출발 준비할게', {}),
    ('C', '지금 몇 시인가요', {}),
    ('D', '어디에 세우지', {}),
])
def test_no_fuzzy_mapping_implicit_context_or_short_alias_substring(catalog, lot, text, context):
    assert place_evidence(catalog, lot, text, context) is None


def test_new_registered_alias_is_used_without_question_specific_rules(catalog):
    row = next(row for row in catalog['items'] if row['lot_id'] == 'D')
    row['language_profile']['name_variants'].append('테스트몰')
    assert place_evidence(catalog, 'D', '테스트몰 안내', {}).source == 'catalog_name'


def test_unseen_typo_never_reads_knowledge_or_calls_api_and_replay_is_cached():
    class Model:
        last_usage = {}
        calls = 0
        def structured(self, system, payload, schema):
            self.calls += 1
            return Decision(intent='guidance', lot_id='D', destination_id=None, space_type=None,
                            preference='nearest', question=None, lookup_type=None, vehicle_value=None)
    class Tools:
        def __getattr__(self, name):
            pytest.fail('No parking API may execute')
    model = Model()
    knowledge = KnowledgeStore()
    service = ConversationService(model, Tools(), knowledge)
    # Initialization reads the catalog/facilities; only dispatch must be forbidden.
    knowledge.get_lot_guidance = lambda _: pytest.fail('No scenario lookup may execute')
    first = service.handle('c', 'r', '신새계 쪽 주차장 안내해줘')
    assert first['status'] == 'clarify'
    assert service._states['c']['context'] == {}
    assert service.handle('c', 'r', '신새계 쪽 주차장 안내해줘') == first
    assert model.calls == 1
