import json
import httpx
import pytest
from backend.app.chatbot_v3.evaluation import score, REQUIRED_CHECKS
from backend.app.chatbot_v3.observability import TraceRecorder, LangfuseSettings, LangfuseExporter
from backend.app.chatbot_v3.conversation import Decision, ConversationService
from backend.app.chatbot_v3.fixture import FixtureModel
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.tools import ParkingTools


def result(intent='vehicle',number='0765'):
    return dict(intent=intent,lot_id=None,lookup_type='plate_suffix',vehicle_value=number,destination_id=None,space_type=None,preference='nearest',question=None)

CASE={'id':'Q','expected':{'accepted_intents':['vehicle','clarify'],'slots_for_action':{'vehicle_value':'0765'},'clarification_may_skip_slots':True}}

def test_scoring_missing_wrong_slots_and_unreviewed_results():
    assert score([CASE],[])['status']=='fail'
    assert score([CASE],[{'id':'Q','decision':result(number='765')}])['results'][0]['result']=='wrong_slots'
    assert score([CASE],[{'id':'Q','decision':result()}])['status']=='needs_review'
    checks={k:True for k in REQUIRED_CHECKS}
    assert score([CASE],[{'id':'Q','decision':result()}],checks)['status']=='pass'
    checks['no_wrong_vehicle_or_lot']=False
    assert score([CASE],[{'id':'Q','decision':result()}],checks)['status']=='fail'
    assert score([CASE],[{'id':'Q','decision':result(intent='clarify')}],checks)['action_resolution_rate']==0
    with pytest.raises(ValueError):score([CASE],[{'id':'unknown','decision':result()}])


def test_recording_redacts_content_and_replay_does_not_add_trace():
    recorder=TraceRecorder()
    client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda r:httpx.Response(404,json={'detail':'single location missing'})))
    with client:
        service=ConversationService(FixtureModel(),ParkingTools(client),KnowledgeStore(),observer=recorder)
        first=service.handle('private-session','private-request','0765 차량 어디 있어?')
        records=recorder.snapshot()
        assert {r['name'] for r in records}=={'coordinator','tools.vehicle','chatbot_v3.turn'}
        assert {r['trace_id'] for r in records}=={first['trace_id']}
        assert 'prompt_sha256' in records[0]['metadata']
        text=json.dumps(records,ensure_ascii=False)
        for private in ('0765','private-session','private-request','차량 어디','single location missing'):assert private not in text
        assert service.handle('private-session','private-request','0765 차량 어디 있어?')==first
        assert recorder.snapshot()==records


def test_broken_recorder_cannot_break_answer():
    class Broken:
        def record(self,**kwargs):raise RuntimeError('private failure')
    with httpx.Client(base_url='http://test') as client:
        s=ConversationService(FixtureModel(),ParkingTools(client),KnowledgeStore(),observer=Broken())
        assert s.handle('c','r','unknown')['status']=='clarify'


def test_recorder_bound_and_usage_allowlist():
    r=TraceRecorder(capacity=1)
    for _ in range(2):r.record(trace_id='t',name='coordinator',kind='generation',start='2026-11-07T00:00:00Z',status='ok',usage={'cost':.1,'total_tokens':3,'secret':'value','prompt_tokens':float('nan')})
    assert len(r.snapshot())==1 and r.dropped==1
    assert r.snapshot()[0]['usage']=={'cost':.1,'total_tokens':3}


def test_export_is_explicit_and_sanitizes_provider_errors():
    settings=LangfuseSettings.load({'LANGFUSE_BASE_URL':'https://trace.example','LANGFUSE_PUBLIC_KEY':'public-test','LANGFUSE_SECRET_KEY':'secret-test'})
    assert 'secret-test' not in repr(settings)
    requests=[]
    def handler(req):
        requests.append(req)
        batch=json.loads(req.content)['batch']
        return httpx.Response(207,json={'successes':[{'id':r['id']} for r in batch],'errors':[]})
    r=TraceRecorder();r.record(trace_id='t',name='coordinator',kind='generation',start='2026-11-07T00:00:00Z',status='ok',usage={'total_tokens':3})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        exporter=LangfuseExporter(c,settings)
        assert not requests
        assert exporter.export(r.snapshot())=={'status':'ok','accepted':1,'rejected':0}
        assert requests[0].url.path=='/api/public/ingestion'
        assert json.loads(requests[0].content)['batch'][0]['body']['usageDetails']=={'total':3}
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(401,json={'detail':'sensitive'}))) as c:
        assert LangfuseExporter(c,settings).export(r.snapshot())=={'status':'unavailable','http_status':401}


def test_export_partial_and_settings_conflict():
    with pytest.raises(ValueError):LangfuseSettings.load({'LANGFUSE_BASE_URL':'https://a','LANGFUSE_HOST':'https://b'})
    with pytest.raises(ValueError):LangfuseSettings.load({'LANGFUSE_BASE_URL':'http://remote.example'})
    settings=LangfuseSettings('https://trace.example','public','secret')
    r=TraceRecorder();r.record(trace_id='t',name='turn',kind='trace',start='2026-11-07T00:00:00Z',status='ok')
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(207,json={'successes':[],'errors':[{'message':'private'}]}))) as c:
        assert LangfuseExporter(c,settings).export(r.snapshot())=={'status':'partial','accepted':0,'rejected':1}
