"""No paid calls: semantic outcomes are supplied, not evaluated here."""
import json
from datetime import datetime, timezone
import httpx
import pytest
from backend.app.chatbot_v3.conversation import ConversationService, Decision, Selection
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.tools import ParkingTools
from backend.app.chatbot_v3.llm import ModelSettings, OpenRouterModel, ModelError


def decision(**kw):
    return Decision(**(dict(intent='availability',lot_id='B',lookup_type=None,vehicle_value=None,destination_id=None,space_type=None,preference='nearest',question=None)|kw))


class Model:
    last_usage={}
    def __init__(self, choices, bad_reporter=False):self.choices=iter(choices);self.payloads=[];self.bad=bad_reporter
    def structured(self, system, payload, schema):
        self.payloads.append(payload)
        if schema is Decision:return next(self.choices)
        return Selection(fact_ids=['fabricated'] if self.bad else [f['id'] for f in payload['facts']])


def setup(choices, **kw):
    requests=[]
    def handler(r):
        requests.append(r)
        stamp=datetime.now(timezone.utc).isoformat()
        if '/vehicles/' in r.url.path:
            return httpx.Response(200,json=dict(lot_id='B',space_id='A-01',car_id='23',status='occupied',session_id='s',observed_at=stamp))
        return httpx.Response(200,json=dict(items=[dict(space_id='A-01',status='empty',space_type='general',version=1,observed_at=stamp)],as_of=stamp))
    client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(handler))
    model=Model(choices,kw.pop('bad_reporter',False))
    return ConversationService(model,ParkingTools(client),KnowledgeStore(),**kw),model,requests,client


def test_context_replay_and_conflict():
    service,model,requests,client=setup([decision(),decision()])
    with client:
        first=service.handle('c','r','기차주차장 자리 있어?')
        assert first['status']=='ok' and '부산역' in first['text'] and '1칸' in first['text']
        assert service.handle('c','r','기차주차장 자리 있어?')==first
        assert len(requests)==1 and len(model.payloads)==2
        with pytest.raises(ValueError):service.handle('c','r','다른 질문')
        service.handle('c','r2','거기는?')
        assert model.payloads[2]['confirmed_context']['lot_id']=='B'
        assert len(requests)==2


def test_vehicle_not_invented_and_new_session_isolated():
    d=decision(intent='vehicle',lookup_type='sticker',vehicle_value='23')
    service,_,requests,client=setup([d,d,d])
    with client:
        assert service.handle('c','1','23번 차 어디?')['status']=='ok'
        assert service.handle('c','2','그차 어디?')['status']=='ok'
        assert service.handle('different','3','내 차 어디?')['status']=='clarify'
        assert len(requests)==2


def test_clarify_no_tools_and_reporter_fallback():
    service,_,requests,client=setup([decision(intent='clarify',lot_id=None,question='어느 주차장인가요?'),decision()],bad_reporter=True)
    with client:
        assert service.handle('c','1','터미널')['status']=='clarify'
        assert not requests
        reply=service.handle('c','2','부산역 빈자리')
        assert reply['status']=='ok' and 'warning' in reply
        assert 'fabricated' not in reply['text']


def test_context_expires():
    now=[0]
    service,model,_,client=setup([decision(),decision(intent='clarify',lot_id=None)],ttl=10,clock=lambda:now[0])
    with client:
        service.handle('c','1','부산역')
        now[0]=11
        service.handle('c','2','거기')
        assert model.payloads[2]['confirmed_context']=={}


def test_model_settings_explicit_file_and_no_secret_repr(tmp_path,monkeypatch):
    for k in ('OPENROUTER_API_KEY','OPENROUTER_MODEL','LLM_MODEL'):monkeypatch.delenv(k,raising=False)
    with pytest.raises(ModelError):ModelSettings.load()
    path=tmp_path/'config.env';path.write_text('OPENROUTER_API_KEY=not-a-real-key\nOPENROUTER_MODEL=test/model\n')
    cfg=ModelSettings.load(path)
    assert cfg.model=='test/model' and 'not-a-real-key' not in repr(cfg)
    monkeypatch.setenv('LLM_MODEL','other/model')
    with pytest.raises(ModelError):ModelSettings.load(path)


def test_openrouter_forced_tool_and_numeric_usage():
    calls=[]
    def handler(r):
        calls.append(json.loads(r.content))
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'submit_result','arguments':'{"fact_ids":["f0"]}'}}]}}],'usage':{'cost':.01,'total_tokens':20,'private':'hidden'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        model=OpenRouterModel(c,ModelSettings('not-a-key','test/model'))
        assert model.structured('prompt',{},Selection).fact_ids==['f0']
        assert calls[0]['tool_choice']['function']['name']=='submit_result'
        assert calls[0]['max_tokens']==700
        assert model.last_usage=={'cost':.01,'total_tokens':20}


@pytest.mark.parametrize('code,body',[(401,{'detail':'secret'}),(200,{'choices':[]})])
def test_provider_failures_are_safe(code,body):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(code,json=body))) as c:
        with pytest.raises(ModelError) as exc:OpenRouterModel(c,ModelSettings('secret','test')).structured('x',{},Selection)
        assert 'secret' not in str(exc.value)


def test_new_vehicle_number_cannot_reuse_previous_number():
    old=decision(intent='vehicle',lookup_type='sticker',vehicle_value='23')
    service,_,requests,client=setup([old,old])
    with client:
        assert service.handle('c','1','23번 차 어디?')['status']=='ok'
        assert service.handle('c','2','45번 차는?')['status']=='clarify'
        assert len(requests)==1


def test_irrelevant_vehicle_fields_cannot_poison_memory():
    service,_,requests,client=setup([decision(lookup_type='sticker',vehicle_value='23')])
    with client:
        assert service.handle('c','1','부산역 빈자리')['status']=='clarify'
        assert not requests
        assert service._states['c']['context']=={}


def test_mock_provenance_survives_to_final_response():
    from backend.app.chatbot_v3.schemas import ToolResult
    service,_,_,client=setup([decision()])
    stamp=datetime.now(timezone.utc).isoformat()
    service.tools.list_spaces=lambda lot:ToolResult(status='ok',is_mock=True,data={'items':[], 'as_of':stamp,'next_cursor':None})
    with client:
        reply=service.handle('c','1','부산역 빈자리')
        assert reply['is_mock'] is True
        assert '모의·시뮬레이션' in reply['text']
        assert reply['retrieved_at']


def test_uncertain_location_not_confirmed_and_fee_not_requested():
    from backend.app.chatbot_v3.schemas import ToolResult
    choices=[decision(intent=intent,lot_id=None,lookup_type='sticker',vehicle_value='23') for intent in ('vehicle','fee')]
    service,_,_,client=setup(choices)
    service.tools.find_vehicle=lambda *a:ToolResult(status='ok',data=dict(lot_id='A',space_id='A-01',car_id='23',session_id='s',status='uncertain',observed_at=datetime.now(timezone.utc).isoformat()))
    def forbidden(*a,**kw):raise AssertionError('fee must not run')
    service.tools.get_fee=forbidden
    with client:
        reply=service.handle('c','1','23번 위치')
        assert '현재 위치는 불확실' in reply['text']
        assert service._states['c']['context']['lot_id']=='A'
        assert service.handle('c','2','요금')['status']=='clarify'


def test_model_outage_is_not_blame_on_user_question():
    service,_,requests,client=setup([])
    def outage(*a):raise ModelError('provider unavailable')
    service.model.structured=outage
    with client:
        assert service.handle('c','1','부산역 빈자리')['status']=='unavailable'
        assert not requests


def test_korean_prompt_files_use_explicit_utf8(monkeypatch):
    from pathlib import Path
    original=Path.read_text
    seen=[]
    def windows_read(self,*args,**kwargs):
        if self.name in ('coordinator.md','reporter.md'):
            assert kwargs.get('encoding')=='utf-8'
            seen.append(self.name)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',windows_read)
    service,_,_,client=setup([])
    client.close()
    assert len(seen)==2
