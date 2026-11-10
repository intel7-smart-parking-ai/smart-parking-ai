"""V53 loopback control UI. External parking events use the existing API only."""
from contextlib import asynccontextmanager
from pathlib import Path
import argparse
from fastapi import FastAPI,Request,HTTPException,Body,File,UploadFile
from fastapi.responses import FileResponse,JSONResponse,Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
import cv2
import numpy as np
from .assets import settings
from .pipeline import ParkingPipeline,public_result
from .registration import read_image
from .inference import decode_image,MAX_BYTES
from .live import LiveSession
from .collection import FrameCollection
from .transport import destination

ROOT=Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    app.state.cfg=None;app.state.pipeline=None;app.state.live=None;app.state.start_error=None
    try:
        app.state.cfg=settings()
        app.state.pipeline=ParkingPipeline(app.state.cfg)
        app.state.live=LiveSession(app.state.pipeline,collection=FrameCollection(ROOT.parent/'runtime'/'collection.db'))
    except Exception as e:
        app.state.start_error=type(e).__name__+' · 모델/기준 사진 경로와 설치 상태를 확인하세요.'
    try:yield
    finally:
        if app.state.live:await run_in_threadpool(app.state.live.close)
        elif app.state.pipeline:app.state.pipeline.close()


app=FastAPI(title='V53 주차장 실시간 통합 인식',version='53.0',lifespan=lifespan)


class BoundedChunkedPost:
    """Bound unknown-length uploads before the multipart parser spools files."""
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method']!='POST':
            return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        if b'content-length' in headers and b'transfer-encoding' not in headers:
            return await self.app(scope,receive,send)
        body=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            chunk=message.get('body',b'')
            if len(body)+len(chunk)>MAX_BYTES+65536:
                response=JSONResponse({'detail':'요청 크기 제한을 초과했습니다.'},status_code=413)
                return await response(scope,receive,send)
            body.extend(chunk)
            if not message.get('more_body'):break
        pending=True
        async def buffered_receive():
            nonlocal pending
            if pending:
                pending=False
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,buffered_receive,send)


app.add_middleware(BoundedChunkedPost)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])


@app.middleware('http')
async def guard(request:Request,call_next):
    if request.method=='POST':
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'현재 프로그램 화면에서 요청하세요.'},status_code=403)
        try:
            if int(request.headers.get('content-length','0'))>MAX_BYTES+65536:
                return JSONResponse({'detail':'사진은 16MB 이하로 선택하세요.'},status_code=413)
        except ValueError:return JSONResponse({'detail':'요청 길이 오류'},status_code=400)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
    return response


def live():
    if not app.state.live:raise HTTPException(503,app.state.start_error or '모델 준비 중')
    return app.state.live


def action(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except (ValueError,RuntimeError) as e:raise HTTPException(409,str(e)) from e


@app.get('/')
def index():return FileResponse(ROOT/'static/index.html')

@app.get('/preview.js')
def preview_script():return FileResponse(ROOT/'static/preview.js',media_type='text/javascript')


@app.get('/health')
def health():
    try:destination();configured=True
    except ValueError:configured=False
    models_ready=app.state.live is not None
    ocr_ready=bool(app.state.pipeline and app.state.pipeline.numbers.ready)
    return {'ready':models_ready and ocr_ready,'models_ready':models_ready,'ocr_ready':ocr_ready,
        'ocr_status':app.state.pipeline.numbers.status if app.state.pipeline else '모델 준비 실패',
        'product_version':'V53','build_revision':35,'error':app.state.start_error,
        'api_configured':configured,'metadata':app.state.pipeline.metadata if app.state.pipeline else None}


@app.post('/live/start')
def start():return action(live().start)


@app.post('/live/stop')
def stop():return action(live().stop)


@app.get('/live/state')
def state(images:bool=True,collected:bool=False,seen1:str='',seen2:str='',wait:bool=False):
    session=live()
    seen={'1':seen1,'2':seen2}
    frame_wait=bool(wait and collected and session.running and session.collection)
    if frame_wait:
        session.collection.wait_for_frame(seen,.6)
    return dict(session.snapshot(include_images=images,collected=collected,seen=seen),frame_wait=frame_wait)


@app.get('/live/delivery-trace')
def delivery_trace():
    return {'recent_outcomes':live().transport.recent_outcomes()}

@app.get('/live/preview/{camera}')
def preview(camera:str,epoch:int=-1,sequence:int=-1):
    if camera not in ('1','2'):raise HTTPException(404,'알 수 없는 카메라')
    status,key,body=live().preview(camera,epoch,sequence)
    headers={'X-Preview-State':status}
    if key:
        headers.update({'X-Frame-Epoch':str(key[0]),'X-Frame-Sequence':str(key[1])})
    if body is None:return Response(status_code=204,headers=headers)
    return Response(content=body,media_type='image/jpeg',headers=headers)


@app.post('/live/config')
def configure(data:dict=Body(...)):
    action(live().configure,data.get('threshold'),data.get('lots'));return {'ok':True}


@app.post('/live/confirm-roi/{camera}')
def confirm(camera:str,data:dict=Body(...)):
    action(live().confirm,camera,reviewed=data.get('reviewed'));return {'ok':True}


@app.post('/live/empty/{camera}')
def empty(camera:str,data:dict=Body(...)):
    if data.get('visually_empty') is not True:raise HTTPException(422,'차량·손·물체가 없는 장면인지 확인하세요.')
    action(live().confirm,camera,empty=True,reviewed=data.get('reviewed'));return {'ok':True}


@app.post('/live/send')
def send(data:dict=Body(...)):
    action(live().enable_send,data.get('enabled'),data.get('simulator_stopped',False));return {'ok':True}


@app.post('/replay/{lot}')
def replay(lot:str):
    if lot not in ('A','B'):raise HTTPException(422,'A 또는 B')
    def analyze_sample():
        with live().photo_turn():
            source={'A':'B','B':'A'}[lot]
            try:frame=read_image(app.state.cfg['team_directory']/'samples'/f'{source}_rectified_sample.jpg')
            except (OSError,ValueError,cv2.error) as error:
                raise HTTPException(503,'예제 사진을 읽을 수 없습니다. 팀 자료의 samples 폴더를 확인하세요.') from error
            return public_result(app.state.pipeline.analyze_photo(frame,camera='replay-'+lot,lot_hint=lot))
    return action(analyze_sample)


@app.post('/analyze-image')
def analyze(file:UploadFile=File(...)):
    # A sync route keeps photo decoding and OpenCV off the ASGI event loop.
    def analyze_upload():
        with live().photo_turn():
            data=file.file.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise HTTPException(413,'사진은 16MB 이하로 선택하세요.')
            try:photo=decode_image(data)
            except ValueError as error:raise HTTPException(422,str(error)) from error
            frame=cv2.cvtColor(np.asarray(photo),cv2.COLOR_RGB2BGR)
            return public_result(app.state.pipeline.analyze_photo(frame))
    try:
        return action(analyze_upload)
    finally:file.file.close()


if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8011)
    args=parser.parse_args();uvicorn.run(app,host='127.0.0.1',port=args.port)
