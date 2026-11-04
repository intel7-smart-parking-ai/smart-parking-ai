"""Camera-free operational adapter. Reuses team billing, sessions and PR46 alerts."""
import asyncio
import sqlite3
import json
from copy import copy
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.app.billing.db import get_db_path, get_db_conn, init_schema, seed_defaults
from backend.app.api.billing_router import billing_router, get_session_fee
from backend.app.api.session_queries import router as sessions_router
from backend.app.alerts.router import router as alerts_router, init_alerts_schema, ingest_led_fire_event, LedFireEvent

LAYOUTS={'A':('basic',12),'B':('angled',16),'C':('loop',12),'D':('mall',64)}
def now():return datetime.now(timezone.utc)
def stamp():return now().isoformat()
@contextmanager
def connect():
    conn=sqlite3.connect(get_db_path(),timeout=15,check_same_thread=False)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:yield conn
    finally:conn.close()
def collection(items):return dict(items=items,next_cursor=None,as_of=stamp())
def initialize():
    get_db_path().parent.mkdir(parents=True,exist_ok=True)
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        init_schema(c);seed_defaults(c);init_alerts_schema(c)
        c.executescript("""
        CREATE TABLE IF NOT EXISTS ui_spaces(space_id TEXT PRIMARY KEY,lot_id TEXT,zone TEXT,space_type TEXT);
        CREATE TABLE IF NOT EXISTS ui_vehicles(car_id TEXT PRIMARY KEY,vehicle_number TEXT UNIQUE,plate_number TEXT);
        CREATE TABLE IF NOT EXISTS ui_simulation(lot_id TEXT PRIMARY KEY,running INTEGER DEFAULT 0,interval_seconds INTEGER DEFAULT 8,next_tick REAL DEFAULT 0,last_error TEXT);
        CREATE TABLE IF NOT EXISTS ui_movements(event_id TEXT PRIMARY KEY,lot_id TEXT,action TEXT,space_id TEXT,car_id TEXT,vehicle_number TEXT,session_id TEXT,occurred_at TEXT);
        CREATE TABLE IF NOT EXISTS ui_metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS ui_active_space ON parking_sessions(space_id) WHERE status='active' AND space_id IS NOT NULL;
        """)
        for lot,(layout,count) in LAYOUTS.items():
            for i in range(count):
                zone=chr(65+i//16) if lot=='D' else ('A' if i<count/2 else 'B')
                kind='ev' if i%16==0 else 'accessible' if i%16==1 else 'general'
                c.execute('INSERT OR IGNORE INTO ui_spaces VALUES(?,?,?,?)',(f'{lot}-{i+1:02}',lot,zone,kind))
            if lot in 'CD':c.execute('INSERT OR IGNORE INTO ui_simulation(lot_id) VALUES(?)',(lot,))
        # Seed only a fresh, empty database; never resurrect vehicles on restart.
        if not c.execute("SELECT 1 FROM ui_metadata WHERE key='seeded'").fetchone():
            if not c.execute('SELECT 1 FROM parking_sessions').fetchone():
                for lot,car,number in [('A','10',None),('A','12',None),('B','13',None),('C','24','9012'),('D','28','3456')]:
                    index=2 if car=='12' else 1
                    c.execute('INSERT INTO parking_sessions VALUES(?,?,?,?,?,?,?)',('initial-'+car,lot,f'{lot}-{index:02}',car,(now()-timedelta(minutes=45)).isoformat(),None,'active'))
                    if number:c.execute('INSERT OR IGNORE INTO ui_vehicles VALUES(?,?,?)',(car,number,'88가'+number))
            c.execute("INSERT INTO ui_metadata VALUES('seeded','1')")
        c.commit()

def spaces_for(c,lot):
    return [dict(r,status='occupied' if r['session_id'] else 'empty',source='simulation',is_mock=True) for r in c.execute("""SELECT p.*,s.session_id,s.car_id,s.entry_at,v.vehicle_number,v.plate_number FROM ui_spaces p LEFT JOIN parking_sessions s ON s.space_id=p.space_id AND s.status='active' LEFT JOIN ui_vehicles v ON v.car_id=s.car_id WHERE p.lot_id=? ORDER BY p.space_id""",(lot,))]

def move(lot,action,body):
    if lot not in 'CD' or len(lot)!=1:raise HTTPException(403,'가상 주차장만 입출차를 실행할 수 있습니다.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        rows=spaces_for(c,lot)
        options=[r for r in rows if r['status']==('empty' if action=='entry' else 'occupied') and (not body.space_id or r['space_id']==body.space_id)]
        if not options:raise HTTPException(409,'입출차할 주차칸이 없습니다.')
        space=options[0];sid=space['session_id'];car=space['car_id'];number=space['vehicle_number']
        if action=='entry':
            number=body.vehicle_number
            if not number:
                number=next((f'{i:04}' for i in range(2000,10000) if not c.execute('SELECT 1 FROM ui_vehicles WHERE vehicle_number=?',(f'{i:04}',)).fetchone()),None)
            vehicle=c.execute('SELECT * FROM ui_vehicles WHERE vehicle_number=?',(number,)).fetchone()
            car=vehicle['car_id'] if vehicle else 'virtual-'+uuid4().hex
            if c.execute("SELECT 1 FROM parking_sessions WHERE car_id=? AND status='active'",(car,)).fetchone():raise HTTPException(409,'이미 주차 중인 차량입니다.')
            if not vehicle:c.execute('INSERT INTO ui_vehicles VALUES(?,?,?)',(car,number,'88가'+number))
            sid=uuid4().hex
            c.execute('INSERT INTO parking_sessions VALUES(?,?,?,?,?,?,?)',(sid,lot,space['space_id'],car,stamp(),None,'active'))
        else:c.execute("UPDATE parking_sessions SET status='closed',exit_at=? WHERE session_id=?",(stamp(),sid))
        event=dict(event_id=uuid4().hex,lot_id=lot,action=action,space_id=space['space_id'],car_id=car,vehicle_number=number,session_id=sid,occurred_at=stamp())
        c.execute('INSERT INTO ui_movements VALUES(:event_id,:lot_id,:action,:space_id,:car_id,:vehicle_number,:session_id,:occurred_at)',event);c.commit()
        return event

class Move(BaseModel):
    space_id:str|None=None
    vehicle_number:str|None=Field(None,pattern=r'^[0-9]{4}$')
    interval_seconds:int=Field(8,ge=3,le=60)
class AlertBody(BaseModel):lot_id:Literal['A','B','C','D']

def create_app():
    @asynccontextmanager
    async def lifespan(app):
        initialize()
        async def tick():
            while True:
                with connect() as c:
                    controls=[dict(r) for r in c.execute('SELECT * FROM ui_simulation WHERE running=1 AND next_tick<=?',(now().timestamp(),))]
                for control in controls:
                    try:
                        with connect() as c:rows=spaces_for(c,control['lot_id'])
                        occupied=sum(r['status']=='occupied' for r in rows)
                        move(control['lot_id'],'exit' if occupied>len(rows)//2 else 'entry',Move())
                        error=None
                    except Exception as exc:error=str(exc)
                    with connect() as c:
                        c.execute('UPDATE ui_simulation SET next_tick=?,last_error=? WHERE lot_id=?',(now().timestamp()+control['interval_seconds'],error,control['lot_id']));c.commit()
                await asyncio.sleep(1)
        task=asyncio.create_task(tick())
        try:yield
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    app=FastAPI(title='PARK:ON 통합 API',lifespan=lifespan)
    @app.get('/api/v1/sessions/{session_id}/fee')
    def session_fee(session_id:str,conn=Depends(get_db_conn)):
        response=get_session_fee(session_id=session_id,now=None,conn=conn)
        data=json.loads(response.body)
        if response.status_code==200:
            receipt=conn.execute("SELECT * FROM mock_payments WHERE session_id=? AND status='completed'",(session_id,)).fetchone()
            data['payment']=dict(receipt) if receipt else None
            if receipt:data['amount_krw']=receipt['amount_krw']
        return JSONResponse(status_code=response.status_code,content=data)
    @app.exception_handler(HTTPException)
    async def http_error(request,exc):return JSONResponse(status_code=exc.status_code,content={'error':dict(code='REQUEST_REJECTED',message=str(exc.detail),details=[])})
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc):return JSONResponse(status_code=422,content={'error':dict(code='VALIDATION_ERROR',message='입력 형식을 확인해주세요.',details=[])})
    @app.get('/api/v1/health')
    def health():
        with connect() as c:c.execute('SELECT 1')
        return dict(status='ok',database='sqlite',camera_connected=False,is_mock=True)
    @app.get('/api/v1/parking-lots')
    def lots():
        with connect() as c:
            return collection([dict(r,spaces=spaces_for(c,r['lot_id']),layout_type=LAYOUTS[r['lot_id']][0],camera_status='not_connected' if r['type']=='real' else 'simulated',updated_at=stamp()) for r in c.execute('SELECT * FROM parking_lots ORDER BY lot_id')])
    @app.get('/api/v1/parking-lots/{lot}/spaces')
    def spaces(lot:str):
        if lot not in LAYOUTS:raise HTTPException(404,'주차장이 없습니다.')
        with connect() as c:return collection(spaces_for(c,lot))
    @app.get('/api/v1/vehicles/{car}/location')
    def location(car:str):
        import re
        if not re.fullmatch('[1-9][0-9]',car):raise HTTPException(422,'스티커는 10~99 두 자리입니다.')
        with connect() as c:
            row=c.execute("SELECT * FROM parking_sessions WHERE car_id=? AND status='active' AND lot_id IN ('A','B')",(car,)).fetchone()
            if not row:raise HTTPException(404,'주차 중인 차량이 없습니다.')
            return dict(row,lookup_type='sticker',is_mock=True,source='simulation')
    @app.get('/api/v1/vehicle-search')
    def search(vehicle_number:str):
        with connect() as c:
            row=c.execute("SELECT s.*,v.vehicle_number,v.plate_number FROM parking_sessions s JOIN ui_vehicles v USING(car_id) WHERE vehicle_number=? AND status='active'",(vehicle_number,)).fetchone()
            if not row:raise HTTPException(404,'주차 중인 차량이 없습니다.')
            return dict(row,lookup_type='plate',is_mock=True)
    @app.post('/api/v1/demo/alerts')
    def alert(body:AlertBody):
        with connect() as c:return ingest_led_fire_event(c,LedFireEvent(event_id=uuid4().hex,lot_id=body.lot_id,source='manual'))[0]
    @app.get('/api/v1/simulation/{lot}')
    def state(lot:str):
        with connect() as c:
            row=c.execute('SELECT * FROM ui_simulation WHERE lot_id=?',(lot,)).fetchone()
            if not row:raise HTTPException(404,'가상 주차장이 없습니다.')
            return dict(row,events=[dict(r) for r in c.execute('SELECT * FROM ui_movements WHERE lot_id=? ORDER BY occurred_at DESC LIMIT 30',(lot,))])
    @app.post('/api/v1/simulation/{lot}/{action}')
    def control(lot:str,action:Literal['entry','exit','start','stop'],body:Move=Move()):
        if action in ('entry','exit'):return move(lot,action,body)
        with connect() as c:
            if not c.execute('SELECT 1 FROM ui_simulation WHERE lot_id=?',(lot,)).fetchone():raise HTTPException(404,'가상 주차장이 없습니다.')
            c.execute('UPDATE ui_simulation SET running=?,interval_seconds=?,next_tick=? WHERE lot_id=?',(action=='start',body.interval_seconds,now().timestamp()+body.interval_seconds,lot));c.commit()
        return state(lot)
    billing_routes=copy(billing_router)
    billing_routes.routes=[r for r in billing_router.routes if r.path!='/api/v1/sessions/{session_id}/fee']
    app.include_router(billing_routes);app.include_router(sessions_router);app.include_router(alerts_router)
    return app
