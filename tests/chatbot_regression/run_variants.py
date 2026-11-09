from client import chat, datetime, json, log, pathlib, req, start, sys, uuid

start('chatbot-1109-structural-variants.json', include_extra_car=False)

def ask(question, session=None, **payload):
    result = chat('variant', question, session, **payload)
    assert result.get('flow') is not None, result
    return result

for question in ('연기 나는 것 같아','지하에 연기가 보여요','차에서 타는 냄새가 나요','불이 번지고 있어요','사람이 다쳤어요'):
    result = ask(question)
    assert '119' in result['text'] and '관리자' in result['text']
    assert result['confirmation_token'] is None

for question in ('일반차인데 전기차 칸 비었으면 대도 돼?', '일반 차량이라서 충전 자리에 주차해도 될까요?',
                 '잠깐 5분만 장애인 칸에 대도 돼?', '장애인 전용 주차칸 이용 가능해?',
                 '전기차인데 일반 칸에 대도 돼?'):
    result = ask(question)
    assert result['meta']['intent'] == 'parking_permission' if 'meta' in result else True
    assert '주차할 수' in result['text'] and not result['card']

for phrase in ('아 됐어 취소','그냥 둘게','아니요','안 할래','취소해줘'):
    session = uuid.uuid4().hex
    result = ask('27번 차 결제해줘', session)
    if result['flow'] == 'confirm_vehicle':
        result = ask('차량 확인', session,chip_id='confirm_yes',confirmation_token=result['confirmation_token'])
    token = result['confirmation_token']
    assert token and result['flow'] == 'confirm_action'
    assert ask('응', session)['confirmation_token'] == token
    assert ask(phrase,session)['confirmation_token'] is None
    assert ask('이전 버튼',session,chip_id='confirm_yes',confirmation_token=token)['confirmation_token'] is None

for question, expected in (
    ('개발원 A-02 상태 알려줘','확인 중'),('개발원 A-06에 주차해도 돼?','이용할 수 없는'),
    ('부산역 B-30 비었어?','칸이 없'),('27번 차 입차 시각 알려줘','입차했어요'),
    ('27번 차 출차 여부 확인','주차 중')):
    assert expected in ask(question)['text']

location = req('/vehicles/27/location')[1]
assert req('/sessions/'+location['session_id']+'/fee')[1]['payment'] is None
print('PASS: 20 phrasing checks and no payment after cancellation')
