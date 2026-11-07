"""Opt-in isolated preview server. Run on loopback:8013; never mounted in main.

Signed expiring leases prevent eviction/restart from silently re-running requests.
Timeouts return to the UI but retain the worker and request record until completion.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import os
import secrets
import time
from typing import Literal
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .budget import turn_budget
from .conversation import ConversationService
from .fixture import EXAMPLES, FixtureModel
from .knowledge import KnowledgeStore
from .llm import ModelSettings, OpenRouterModel
from .tools import ParkingTools


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    session_id: str = Field(min_length=1,max_length=128)
    runtime_id: str = Field(min_length=1,max_length=128)
    request_id: str = Field(min_length=1,max_length=128)
    text: str = Field(min_length=1,max_length=200)
    language: Literal['ko'] = 'ko'


class PreviewError(Exception):
    def __init__(self,status,code,message):
        self.status,self.code,self.message=status,code,message


@dataclass
class Session:
    service: ConversationService
    close: object
    issued: float
    jobs: dict = field(default_factory=dict)
    busy: bool = False


class PreviewGateway:
    def __init__(self,factory,*,mode='fixture',ttl=1800,capacity=100,max_requests=1000,
                 workers=2,response_timeout=18,clock=time.monotonic):
        self.factory,self.mode,self.ttl,self.capacity=factory,mode,ttl,capacity
        self.max_requests,self.workers,self.response_timeout=max_requests,workers,response_timeout
        self.clock=clock
        self.runtime_id=uuid.uuid4().hex
        self.secret=secrets.token_bytes(32)
        self.sessions={}
        self.running=0
        self.closed=False

    def _prune(self):
        for sid,session in list(self.sessions.items()):
            if not session.busy and self.clock()-session.issued >= self.ttl:
                session.close()
                del self.sessions[sid]

    def config(self):
        self._prune()
        payload=f'{int(self.clock()*1000)}.{uuid.uuid4().hex}'
        signature=hmac.new(self.secret,payload.encode(),hashlib.sha256).hexdigest()[:32]
        return dict(runtime_id=self.runtime_id,session_id=payload+'.'+signature,
                    model_mode=self.mode,supported_languages=['ko'],examples=EXAMPLES,
                    session_ttl_seconds=self.ttl)

    def _issued(self,sid):
        try:
            stamp,nonce,signature=sid.split('.')
            expected=hmac.new(self.secret,f'{stamp}.{nonce}'.encode(),hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(signature,expected):raise ValueError()
            issued=int(stamp)/1000
            if not 0 <= self.clock()-issued < self.ttl:raise ValueError()
            return issued
        except (ValueError,TypeError):
            raise PreviewError(409,'SESSION_EXPIRED','대화가 만료됐거나 유효하지 않습니다. 새 대화를 시작해주세요.')

    def _run(self,session,body,deadline):
        try:
            with turn_budget(deadline):
                result=session.service.handle(body.session_id,body.request_id,body.text)
            text=result['text']
            return dict(text=text,messages=[{'text':text}],chips=[],card=None,flow='idle',
                        confirmation_token=None,vehicle_context=None,banner=None,safety=None,
                        runtime_id=self.runtime_id,language='ko',
                        meta={k:v for k,v in result.items() if k not in ('text','cards')})
        except ValueError:
            raise PreviewError(409,'SESSION_LIMIT','대화 요청을 처리할 수 없습니다. 새 대화를 시작해주세요.')
        except Exception:
            # Retain this task's failure too: a fee lookup might have completed.
            raise PreviewError(503,'REQUEST_FAILED','요청 처리를 확인하지 못했습니다. 같은 요청을 자동 재실행하지 않습니다.')

    def _finished(self,session,task):
        self.running-=1
        session.busy=False
        # Retrieve exceptions even when the browser already timed out.
        if not task.cancelled():task.exception()
        self._prune()

    async def handle(self,body):
        if self.closed:raise PreviewError(503,'BUSY','시험 서버가 종료 중입니다.')
        if body.runtime_id != self.runtime_id:
            raise PreviewError(409,'RUNTIME_CHANGED','서버가 다시 시작됐습니다. 새 대화를 시작해주세요.')
        issued=self._issued(body.session_id)
        if not body.text.strip():raise PreviewError(400,'INVALID_INPUT','질문을 입력해주세요.')
        self._prune()
        session=self.sessions.get(body.session_id)
        if session and body.request_id in session.jobs:
            old_text,job=session.jobs[body.request_id]
            if old_text != body.text:raise PreviewError(409,'REQUEST_CONFLICT','같은 요청 ID의 질문을 바꿀 수 없습니다.')
            if not job.done():raise PreviewError(409,'REQUEST_PENDING','이 요청은 아직 처리 중입니다. 같은 요청을 다시 확인해주세요.')
            return job.result()
        if session and len(session.jobs)>=self.max_requests:
            raise PreviewError(409,'SESSION_LIMIT','대화 요청 한도에 도달했습니다. 새 대화를 시작해주세요.')
        if session and session.busy:
            raise PreviewError(409,'REQUEST_PENDING','이 대화의 이전 요청이 처리 중입니다.')
        if self.running>=self.workers:
            raise PreviewError(503,'BUSY','동시 처리 한도에 도달했습니다. 같은 요청을 잠시 후 다시 확인해주세요.')
        if session is None:
            if len(self.sessions)>=self.capacity:
                raise PreviewError(503,'BUSY','대화 보관 한도에 도달했습니다. 만료 후 다시 시도해주세요.')
            service,close=self.factory()
            session=Session(service,close,issued)
            self.sessions[body.session_id]=session
        self.running+=1
        session.busy=True
        deadline=time.monotonic()+self.response_timeout
        job=asyncio.create_task(asyncio.to_thread(self._run,session,body,deadline))
        session.jobs[body.request_id]=(body.text,job)
        job.add_done_callback(lambda task:self._finished(session,task))
        try:
            return await asyncio.wait_for(asyncio.shield(job),timeout=self.response_timeout)
        except asyncio.TimeoutError:
            raise PreviewError(504,'RESPONSE_TIMEOUT','응답 대기 시간이 지났습니다. 처리 결과는 같은 요청으로 다시 확인해주세요.')

    async def shutdown(self):
        self.closed=True
        jobs=[task for s in self.sessions.values() for _,task in s.jobs.values() if not task.done()]
        # Do not release clients while a request may still be using them.
        if jobs:await asyncio.gather(*jobs,return_exceptions=True)
        for session in list(self.sessions.values()):session.close()
        self.sessions.clear()


def service_factory(mode,backend_url,settings=None):
    knowledge=KnowledgeStore()
    def build():
        api=httpx.Client(base_url=backend_url)
        model_client=httpx.Client() if mode=='openrouter' else None
        model=OpenRouterModel(model_client,settings) if model_client else FixtureModel()
        service=ConversationService(model,ParkingTools(api),knowledge)
        def close():
            api.close()
            if model_client:model_client.close()
        return service,close
    return build


def create_preview_app(*,gateway=None):
    if gateway is None:
        mode=os.getenv('CHATBOT_V3_MODEL_MODE','fixture')
        if mode not in ('fixture','openrouter'):raise ValueError('CHATBOT_V3_MODEL_MODE must be fixture or openrouter')
        settings=ModelSettings.load(os.getenv('CHATBOT_V3_ENV_FILE')) if mode=='openrouter' else None
        gateway=PreviewGateway(service_factory(mode,os.getenv('BACKEND_BASE_URL','http://127.0.0.1:8000'),settings),mode=mode)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await gateway.shutdown()

    app=FastAPI(title='PARK:ON v3 isolated preview',lifespan=lifespan)
    app.state.gateway=gateway

    @app.middleware('http')
    async def no_store(request,call_next):
        # Preview is local development, not an authenticated deployment endpoint.
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).hostname not in ('localhost','127.0.0.1','::1'):
                return JSONResponse(status_code=403,content={'error':{'code':'ORIGIN_DENIED','message':'로컬 시험 화면에서만 사용해주세요.','details':[]}})
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(PreviewError)
    async def handle_error(request,exc):
        return JSONResponse(status_code=exc.status,content={'error':{'code':exc.code,'message':exc.message,'details':[]}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse(status_code=400,content={'error':{'code':'INVALID_INPUT','message':'한국어 질문 1~200자와 대화·요청 식별자를 확인해주세요.','details':[]}})

    @app.get('/api/v1/chat/config')
    async def config():return gateway.config()

    @app.post('/api/v1/chat')
    async def chat(body:ChatRequest):return await gateway.handle(body)

    return app
