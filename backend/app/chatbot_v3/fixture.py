"""Exact sample decisions for wiring tests, deliberately not an NLP fallback."""
from .conversation import Decision, Selection

EXAMPLES = ['부산역 빈자리 있어?', '기차주차장 자리 있어?', '영화관 가까운 빈자리 추천해줘',
            '신세계 시설 알려줘', '국제선 주차장 전기차 빈자리 있어?', '23번 차 어디 있어?', '23번 차 요금 알려줘']

class FixtureModel:
    last_usage = {}
    def structured(self, system, payload, schema):
        if schema is Selection:
            return Selection(fact_ids=[f['id'] for f in payload['facts']])
        base = dict(intent='clarify',lot_id=None,lookup_type=None,vehicle_value=None,
                    destination_id=None,space_type=None,preference='nearest',
                    question='모의 모델은 화면의 예시 질문만 처리합니다. 예시를 선택해주세요.')
        choices = [dict(intent='availability',lot_id='B'),dict(intent='availability',lot_id='B'),
                   dict(intent='recommend',lot_id='D',destination_id='D_cinema'),
                   dict(intent='facilities',lot_id='D'),dict(intent='availability',lot_id='C',space_type='ev'),
                   dict(intent='vehicle',lookup_type='sticker',vehicle_value='23'),
                   dict(intent='fee',lookup_type='sticker',vehicle_value='23')]
        if payload['question'] in EXAMPLES:
            base.update(choices[EXAMPLES.index(payload['question'])],question=None)
        return Decision(**base)
