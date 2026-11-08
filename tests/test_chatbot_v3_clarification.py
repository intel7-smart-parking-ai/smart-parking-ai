import pytest
from backend.app.chatbot_v3.conversation import ConversationService, Decision
from backend.app.chatbot_v3.knowledge import KnowledgeStore


@pytest.mark.parametrize('text,question,expected', [
    ('신새계 쪽 주차장 안내해줘', '신세계 쪽 주차장 중 어느 곳을 안내해 드릴까요?', None),
    ('신새게 주차장 빈자리 알려줘', '신새게 주차장이 신세계백화점주차장을 의미하나요?', None),
    ('신새계 주차장', 'D 주차장을 뜻하나요?', None),
    ('신새계 주차장', '신 세계 주차장을 뜻하나요?', None),
    ('부산역 말고 공항', '부산역 주차장을 말씀하시나요?', None),
    ('신새계 주차장', '정확한 주차장 이름을 알려주세요.', '정확한 주차장 이름을 알려주세요.'),
    ('신세계 시설', '신세계의 어떤 시설을 찾으세요?', '신세계의 어떤 시설을 찾으세요?'),
    ('１２３４ 차량', 'ASCII 숫자로 다시 알려주세요.', 'ASCII 숫자로 다시 알려주세요.'),
    ('내 차', '차량 식별 번호를 알려주세요.', '차량 식별 번호를 알려주세요.'),
])
def test_clarification_never_suggests_unsupported_place(text, question, expected):
    class Model:
        calls = 0
        def structured(self, prompt, payload, schema):
            self.calls += 1
            return Decision(intent='clarify', lot_id=None, lookup_type=None, vehicle_value=None,
                            destination_id=None, space_type=None, preference='nearest', question=question)
    model = Model()
    # Any accidental parking dispatch fails because no tools are provided.
    service = ConversationService(model, object(), KnowledgeStore())
    result = service.handle('c', 'r', text)
    assert result['status'] == 'clarify'
    assert result['text'] == (expected or '어느 주차장을 말씀하시는지 이름을 확인해 주세요.')
    assert service._states['c']['context'] == {}
    assert service.handle('c', 'r', text) == result
    assert model.calls == 1
