import asyncio
import threading
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.chatbot_v3.preview import PreviewGateway, PreviewError, ChatRequest, create_preview_app
from backend.app.chatbot_v3.conversation import ConversationService
from backend.app.chatbot_v3.fixture import FixtureModel
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.tools import ParkingTools


def body(config,rid='1',text='부산역 빈자리 있어?'):
    return ChatRequest(**{k:config[k] for k in ('runtime_id','session_id')},request_id=rid,text=text)

class Stub:
    def __init__(self,gate=None):self.calls=0;self.gate=gate
    def handle(self,*args):
        self.calls+=1
        if self.gate:self.gate.wait(2)
        return {'text':'조회 완료','status':'ok','cards':[],'source_refs':['test']}


def test_http_contract_validation_and_deduplication():
    service=Stub()
    gateway=PreviewGateway(lambda:(service,lambda:None))
    with TestClient(create_preview_app(gateway=gateway)) as c:
        config=c.get('/api/v1/chat/config').json()
        payload=body(config).model_dump()
        first=c.post('/api/v1/chat',json=payload)
        assert first.status_code==200
        assert first.headers['cache-control']=='no-store'
        assert first.json()['messages'][0]['text']==first.json()['text']
        assert c.post('/api/v1/chat',json=payload).json()==first.json()
        assert service.calls==1
        assert c.post('/api/v1/chat',json=payload|{'text':'different'}).status_code==409
        assert c.post('/api/v1/chat',json=payload|{'text':'가'*201}).status_code==400
        assert c.post('/api/v1/chat',json=payload|{'language':'en'}).status_code==400
        assert c.post('/api/v1/chat',json=payload|{'runtime_id':'old'}).json()['error']['code']=='RUNTIME_CHANGED'
        assert c.post('/api/v1/chat',json=payload,headers={'origin':'https://evil.invalid'}).status_code==403


def test_timeout_keeps_worker_and_replays_result_without_reexecution():
    async def run():
        gate=threading.Event();service=Stub(gate)
        gateway=PreviewGateway(lambda:(service,lambda:None),workers=1,response_timeout=.03)
        req=body(gateway.config())
        with pytest.raises(PreviewError) as exc:await gateway.handle(req)
        assert exc.value.code=='RESPONSE_TIMEOUT'
        assert gateway.running==1
        with pytest.raises(PreviewError) as exc:await gateway.handle(req)
        assert exc.value.code=='REQUEST_PENDING'
        with pytest.raises(PreviewError) as exc:await gateway.handle(body(gateway.config()))
        assert exc.value.code=='BUSY'
        gate.set()
        await asyncio.gather(*(job for session in gateway.sessions.values() for _,job in session.jobs.values()))
        assert (await gateway.handle(req))['text']=='조회 완료'
        assert service.calls==1
        await gateway.shutdown()
    asyncio.run(run())


def test_independent_sessions_can_run_concurrently():
    async def run():
        gate=threading.Event();services=[]
        def factory():
            s=Stub(gate);services.append(s);return s,lambda:None
        gateway=PreviewGateway(factory,workers=2,response_timeout=1)
        jobs=[asyncio.create_task(gateway.handle(body(gateway.config()))) for _ in range(2)]
        for _ in range(100):
            if len(services)==2 and all(s.calls==1 for s in services):break
            await asyncio.sleep(.005)
        assert len(services)==2 and all(s.calls==1 for s in services)
        gate.set();await asyncio.gather(*jobs);await gateway.shutdown()
    asyncio.run(run())


def test_expired_lease_cannot_resurrect_evicted_requests():
    async def run():
        now=[100];closed=[]
        gateway=PreviewGateway(lambda:(Stub(),lambda:closed.append(True)),ttl=10,capacity=1,max_requests=1,clock=lambda:now[0])
        req=body(gateway.config());await gateway.handle(req)
        with pytest.raises(PreviewError) as exc:await gateway.handle(req.model_copy(update={'request_id':'2'}))
        assert exc.value.code=='SESSION_LIMIT'
        now[0]=111
        fresh=gateway.config();assert closed
        with pytest.raises(PreviewError) as exc:await gateway.handle(req)
        assert exc.value.code=='SESSION_EXPIRED'
        await gateway.handle(body(fresh));await gateway.shutdown()
    asyncio.run(run())


def test_slow_coordinator_does_not_start_late_fee_lookup():
    async def run():
        from backend.app.chatbot_v3.conversation import Decision
        gate=threading.Event();calls=[]
        class SlowModel:
            def structured(self,*args):
                gate.wait(2)
                return Decision(intent='fee',lot_id=None,lookup_type='sticker',vehicle_value='23',destination_id=None,space_type=None,preference='nearest',question=None)
        client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda r:calls.append(r)))
        service=ConversationService(SlowModel(),ParkingTools(client),KnowledgeStore())
        gateway=PreviewGateway(lambda:(service,client.close),response_timeout=.02)
        req=body(gateway.config(),text='23번 차 요금 알려줘')
        with pytest.raises(PreviewError):await gateway.handle(req)
        gate.set()
        await asyncio.gather(*(job for session in gateway.sessions.values() for _,job in session.jobs.values()))
        assert (await gateway.handle(req))['meta']['status']=='unavailable'
        assert not calls
        await gateway.shutdown()
    asyncio.run(run())


def test_fixture_uses_real_tool_interface_not_fabricated_spaces():
    from datetime import datetime,timezone
    calls=[]
    def handler(r):
        calls.append(r.url.path)
        return httpx.Response(200,headers={'X-ParkFlow-Data-Source':'mock'},json={'items':[], 'as_of':datetime.now(timezone.utc).isoformat()})
    client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(handler))
    service=ConversationService(FixtureModel(),ParkingTools(client),KnowledgeStore())
    gateway=PreviewGateway(lambda:(service,client.close))
    with TestClient(create_preview_app(gateway=gateway)) as c:
        config=c.get('/api/v1/chat/config').json()
        reply=c.post('/api/v1/chat',json=body(config).model_dump()).json()
        assert '0칸' in reply['text'] and '모의' in reply['text']
        assert calls==['/api/v1/parking-lots/B/spaces']
        unknown=c.post('/api/v1/chat',json=body(config,'2','아무 질문').model_dump()).json()
        assert unknown['meta']['status']=='clarify' and len(calls)==1
