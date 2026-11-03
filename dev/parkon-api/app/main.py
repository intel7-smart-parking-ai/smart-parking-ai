from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import uuid4
from typing import Literal
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ConfigDict
from .db import connect, initialize
from .billing import quote, FIXED_TIME

class APIError(Exception):
    def __init__(self, message, code='INVALID_REQUEST', status=400):
        self.message, self.code, self.status = message, code, status

@asynccontextmanager
async def lifespan(app):
    initialize()
    yield

app = FastAPI(title='PARK:ON Docker Demo API', version='0.1.0', lifespan=lifespan, description='PostgreSQL-backed demo; billing provider is a server fixture, not the official module. No real PG/payment integration. Local demo role switching is not authentication.')

@app.exception_handler(APIError)
async def api_error(request: Request, exc: APIError):
    return JSONResponse(status_code=exc.status, content={'error':{'code':exc.code,'message':exc.message,'details':[]}})

def collection(items):
    return {'items':items,'next_cursor':None,'as_of':FIXED_TIME}

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details=[{'loc':list(e['loc']),'type':e['type'],'message':e['msg']} for e in exc.errors()]
    return JSONResponse(status_code=422,content={'error':{'code':'VALIDATION_ERROR','message':'입력 형식을 확인해주세요.','details':details}})

@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code,content={'error':{'code':'ENDPOINT_NOT_FOUND' if exc.status_code==404 else 'HTTP_ERROR','message':str(exc.detail),'details':[]}})

@app.get('/api/v1/health')
def health():
    with connect() as conn: conn.execute('SELECT 1')
    return {'status':'ok','database':'postgresql','billing_provider':'server_fixture','is_mock':True}

@app.get('/api/v1/parking-lots')
def lots():
    with connect() as conn:
        result = conn.execute('SELECT * FROM parking_lots ORDER BY lot_id').fetchall()
        for lot in result:
            lot['spaces'] = conn.execute('SELECT s.*, v.vehicle_number, v.plate_number FROM parking_spaces s LEFT JOIN demo_vehicles v USING(car_id) WHERE s.lot_id=%s ORDER BY s.space_id',(lot['lot_id'],)).fetchall()
            lot['camera_status'] = 'not_connected' if lot['type']=='real' else 'simulated'
            lot['updated_at'] = FIXED_TIME
        return collection(result)

@app.get('/api/v1/parking-lots/{lot_id}/spaces')
def spaces(lot_id: str):
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM parking_lots WHERE lot_id=%s',(lot_id,)).fetchone(): raise APIError('주차장을 찾을 수 없습니다.','PARKING_LOT_NOT_FOUND',404)
        return collection(conn.execute('SELECT * FROM parking_spaces WHERE lot_id=%s ORDER BY space_id',(lot_id,)).fetchall())

@app.get('/api/v1/rate-policies')
def policies():
    with connect() as conn:
        return collection([{'lot_id':r['lot_id'], **r['policy'], 'authority':'server_fixture'} for r in conn.execute('SELECT * FROM rate_policies ORDER BY lot_id').fetchall()])

@app.get('/api/v1/sessions')
def sessions(lot_id: str | None = None):
    with connect() as conn:
        return collection(conn.execute('SELECT s.*, p.bay_label AS space_label, v.plate_number FROM parking_sessions s JOIN parking_spaces p ON p.space_id=s.space_id LEFT JOIN demo_vehicles v ON v.car_id=s.car_id WHERE (%s::text IS NULL OR s.lot_id=%s) ORDER BY s.car_id',(lot_id,lot_id)).fetchall())

@app.get('/api/v1/vehicles/{car_id}/location')
def location(car_id: str):
    validate_sticker(car_id)
    with connect() as conn:
        row = conn.execute("SELECT * FROM parking_sessions WHERE car_id=%s AND status='parked'",(car_id,)).fetchone()
        if not row: raise APIError('현재 주차 중인 차량을 찾지 못했습니다.','VEHICLE_NOT_FOUND',404)
        return row

def get_quote(conn, session_id):
    session = conn.execute('SELECT * FROM parking_sessions WHERE session_id=%s',(session_id,)).fetchone()
    if not session: raise APIError('주차 세션을 찾을 수 없습니다.','SESSION_NOT_FOUND',404)
    policy = conn.execute('SELECT policy FROM rate_policies WHERE lot_id=%s',(session['lot_id'],)).fetchone()['policy']
    return session, quote(session, policy)

@app.get('/api/v1/sessions/{session_id}/fee')
def fee(session_id: str):
    with connect() as conn:
        _, result = get_quote(conn,session_id)
        result['payment'] = conn.execute("SELECT * FROM mock_payments WHERE session_id=%s AND status='completed'",(session_id,)).fetchone()
        latest=conn.execute('SELECT status FROM mock_payments WHERE session_id=%s ORDER BY created_at DESC,payment_id DESC LIMIT 1',(session_id,)).fetchone()
        result['payment_status'] = latest['status'] if latest else 'unpaid'
        return result

def lookup_result(conn, rows, lookup_type):
    if not rows:
        message = '해당 스티커 번호로 실물 주차장에 주차 중인 차량이 없습니다.' if lookup_type == 'sticker' else '해당 차량번호로 가상 주차장에 주차 중인 차량이 없습니다.'
        raise APIError(message, 'VEHICLE_NOT_FOUND', 404)
    if len(rows) > 1:
        raise APIError('같은 번호의 주차 기록이 여러 개입니다. 관리자에게 확인해주세요.', 'AMBIGUOUS_VEHICLE', 409)
    result = rows[0]
    result['lookup_type'] = lookup_type
    if lookup_type == 'sticker':
        result.pop('plate_number', None)
        result.pop('vehicle_number', None)
    _, result['fee'] = get_quote(conn, result['session_id'])
    result['fee']['payment'] = conn.execute("SELECT * FROM mock_payments WHERE session_id=%s AND status='completed'", (result['session_id'],)).fetchone()
    latest=conn.execute('SELECT status FROM mock_payments WHERE session_id=%s ORDER BY created_at DESC,payment_id DESC LIMIT 1',(result['session_id'],)).fetchone()
    result['fee']['payment_status'] = latest['status'] if latest else 'unpaid'
    return result

@app.get('/api/v1/vehicle-search')
def search_vehicle(vehicle_number: str = Query(pattern=r'^[0-9]{4}$')):
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.*, v.vehicle_number, v.plate_number, p.bay_label AS space_label FROM parking_sessions s JOIN demo_vehicles v USING(car_id) JOIN parking_spaces p ON p.space_id=s.space_id WHERE v.vehicle_number=%s AND s.lot_id IN ('C','D') AND s.status='parked' ORDER BY s.entry_at DESC",
            (vehicle_number,),
        ).fetchall()
        return lookup_result(conn, rows, 'plate')

def validate_sticker(car_id):
    import re
    if re.fullmatch(r'[0-9]',car_id):
        raise APIError('스티커 번호는 두 자리로 입력해주세요. (10~99)','INVALID_CAR_ID',422)
    if not re.fullmatch(r'[1-9][0-9]',car_id):
        raise APIError('스티커 번호 10~99를 확인해주세요. 00~09는 사용하지 않습니다.','INVALID_CAR_ID',422)

@app.get('/api/v1/sticker-search')
def search_sticker(car_id: str):
    validate_sticker(car_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.*, p.bay_label AS space_label FROM parking_sessions s JOIN parking_spaces p ON p.space_id=s.space_id WHERE s.car_id=%s AND s.lot_id IN ('A','B') AND s.status='parked' ORDER BY s.entry_at DESC",
            (car_id,),
        ).fetchall()
        return lookup_result(conn, rows, 'sticker')

class PaymentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1,max_length=200)
    model_config = ConfigDict(extra='forbid')
    payment_method: Literal['MOCK_CARD', 'MOCK_EASY_PAY', 'MOCK_ACCOUNT'] = 'MOCK_CARD'

@app.post('/api/v1/mock-payments')
def pay(body: PaymentRequest):
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('key:'+body.idempotency_key,))
        old = conn.execute('SELECT * FROM mock_payments WHERE idempotency_key=%s',(body.idempotency_key,)).fetchone()
        if old:
            if old['session_id']!=body.session_id: raise APIError('다른 차량에 사용된 결제 요청 키입니다.','IDEMPOTENCY_CONFLICT',409)
            if old['payment_method']!=body.payment_method: raise APIError('이미 처리된 결제 수단과 다릅니다. 요금을 다시 조회해주세요.','IDEMPOTENCY_CONFLICT',409)
            return old
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('session:'+body.session_id,))
        session, result = get_quote(conn,body.session_id)
        if conn.execute("SELECT 1 FROM mock_payments WHERE session_id=%s AND status='completed'",(body.session_id,)).fetchone(): raise APIError('이미 결제 완료된 차량입니다.','ALREADY_PAID',409)
        return conn.execute("INSERT INTO mock_payments(payment_id,session_id,lot_id,car_id,amount_krw,status,idempotency_key,created_at,payment_method) VALUES(%s,%s,%s,%s,%s,'completed',%s,%s,%s) RETURNING *",('pay-'+uuid4().hex[:12],body.session_id,session['lot_id'],session['car_id'],result['amount_krw'],body.idempotency_key,FIXED_TIME,body.payment_method)).fetchone()

@app.get('/api/v1/mock-payments')
def payments(lot_id: str | None=None, status: str | None=None):
    with connect() as conn:
        return collection(conn.execute('SELECT * FROM mock_payments WHERE (%s::text IS NULL OR lot_id=%s) AND (%s::text IS NULL OR status=%s) ORDER BY created_at DESC,payment_id',(lot_id,lot_id,status,status)).fetchall())

@app.post('/api/v1/mock-payments/{payment_id}/cancel')
def cancel(payment_id: str):
    with connect() as conn:
        row=conn.execute('SELECT * FROM mock_payments WHERE payment_id=%s FOR UPDATE',(payment_id,)).fetchone()
        if not row: raise APIError('결제를 찾을 수 없습니다.','PAYMENT_NOT_FOUND',404)
        if row['status']=='canceled': return row
        return conn.execute("UPDATE mock_payments SET status='canceled',canceled_at=%s WHERE payment_id=%s RETURNING *",(FIXED_TIME,payment_id)).fetchone()

@app.get('/api/v1/reports/revenue')
def revenue(month: str | None=None, date: str | None=None, lot_id: str | None=None):
    if month and date: raise APIError('일별 또는 월별 중 하나를 선택해주세요.','INVALID_PERIOD')
    try:
        if month:
            start=datetime.strptime(month,'%Y-%m').date()
            if start.strftime('%Y-%m')!=month: raise ValueError()
            end=start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
        elif date:
            from datetime import date as Date, timedelta
            start=Date.fromisoformat(date); end=start+timedelta(days=1)
        else: start=end=None
    except ValueError: raise APIError('조회 기간을 확인해주세요.','INVALID_PERIOD')
    where="(%s::date IS NULL OR (p.created_at AT TIME ZONE 'Asia/Seoul')::date >= %s) AND (%s::date IS NULL OR (p.created_at AT TIME ZONE 'Asia/Seoul')::date < %s)"
    params=(start,start,end,end)
    with connect() as conn:
        rows=conn.execute(f"""SELECT l.lot_id, COALESCE(sum(p.amount_krw) FILTER(WHERE p.status='completed'),0)::bigint amount_krw,
          COALESCE(sum(p.amount_krw),0)::bigint gross_krw, COALESCE(sum(p.amount_krw) FILTER(WHERE p.status='canceled'),0)::bigint canceled_krw,
          count(p.payment_id) FILTER(WHERE p.status='completed') payment_count, count(p.payment_id) FILTER(WHERE p.status='canceled') canceled_count
          FROM parking_lots l LEFT JOIN mock_payments p ON p.lot_id=l.lot_id AND {where}
          WHERE (%s::text IS NULL OR l.lot_id=%s) GROUP BY l.lot_id ORDER BY l.lot_id""",(*params,lot_id,lot_id)).fetchall()
        daily=conn.execute(f"""SELECT (p.created_at AT TIME ZONE 'Asia/Seoul')::date date, COALESCE(sum(p.amount_krw) FILTER(WHERE p.status='completed'),0)::bigint amount_krw, COALESCE(sum(p.amount_krw),0)::bigint gross_krw, COALESCE(sum(p.amount_krw) FILTER(WHERE p.status='canceled'),0)::bigint canceled_krw, count(*) FILTER(WHERE p.status='completed') payment_count, count(*) FILTER(WHERE p.status='canceled') canceled_count
          FROM mock_payments p WHERE {where} AND (%s::text IS NULL OR p.lot_id=%s) GROUP BY 1 ORDER BY 1""",(*params,lot_id,lot_id)).fetchall()
    summary={key:sum(r[key] for r in rows) for key in ['amount_krw','gross_krw','canceled_krw','payment_count','canceled_count']}
    def totals(row):
        return dict(gross_amount_krw=row['gross_krw'],canceled_amount_krw=row['canceled_krw'],net_revenue_krw=row['amount_krw'],completed_count=row['payment_count'],canceled_count=row['canceled_count'])
    daily_items=[dict(date=str(r['date']),gross_krw=r['gross_krw'],canceled_krw=r['canceled_krw'],net_krw=r['amount_krw'],completed_count=r['payment_count'],canceled_count=r['canceled_count']) for r in daily]
    return dict(summary={**totals(summary),'by_type':{}},lots=[dict(lot_id=r['lot_id'],**totals(r)) for r in rows],daily_revenue=daily_items,monthly_revenue=[],filter_month=date or month or 'ALL',lot_id=lot_id,aggregated_basis='original_payment_period',is_mock=True)

@app.get('/api/v1/alerts')
def alerts(lot_id: str | None=None,status: str | None=None):
    with connect() as conn:
        return collection(conn.execute('SELECT a.*, p.bay_label AS space_label FROM alerts a LEFT JOIN parking_spaces p ON p.space_id=a.space_id WHERE (%s::text IS NULL OR a.lot_id=%s) AND (%s::text IS NULL OR a.status=%s) ORDER BY a.detected_at DESC,a.alert_id',(lot_id,lot_id,status,status)).fetchall())

class AlertRequest(BaseModel):
    lot_id: str

@app.post('/api/v1/demo/alerts')
def create_alert(body: AlertRequest):
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM parking_lots WHERE lot_id=%s',(body.lot_id,)).fetchone(): raise APIError('주차장을 찾을 수 없습니다.','PARKING_LOT_NOT_FOUND',404)
        return conn.execute("INSERT INTO alerts(alert_id,lot_id,space_id,type,status,detected_at) VALUES(%s,%s,%s,'mock_fire_led_observed','active',%s) RETURNING *",('alert-'+uuid4().hex[:12],body.lot_id,None,FIXED_TIME)).fetchone()

@app.post('/api/v1/alerts/{alert_id}/acknowledge')
def acknowledge(alert_id: str):
    with connect() as conn:
        row=conn.execute('SELECT * FROM alerts WHERE alert_id=%s FOR UPDATE',(alert_id,)).fetchone()
        if not row: raise APIError('경보를 찾을 수 없습니다.','ALERT_NOT_FOUND',404)
        if row['status']!='active': return row
        return conn.execute("UPDATE alerts SET status='acknowledged',acknowledged_at=%s WHERE alert_id=%s RETURNING *",(FIXED_TIME,alert_id)).fetchone()

@app.post('/api/v1/alerts/{alert_id}/resolve')
def resolve(alert_id: str):
    with connect() as conn:
        row=conn.execute('SELECT * FROM alerts WHERE alert_id=%s FOR UPDATE',(alert_id,)).fetchone()
        if not row: raise APIError('경보를 찾을 수 없습니다.','ALERT_NOT_FOUND',404)
        if row['status']=='active': raise APIError('먼저 경보를 확인해주세요.','INVALID_STATE',409)
        if row['status']=='resolved': return row
        return conn.execute("UPDATE alerts SET status='resolved',resolved_at=%s WHERE alert_id=%s RETURNING *",(FIXED_TIME,alert_id)).fetchone()
