"""Demo-only C/D movement. No camera, payment or team API mutations."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from .db import connect

KST=timezone(timedelta(hours=9))
class SimulationError(Exception):
    def __init__(self,message,code='SIMULATION_CONFLICT',status=409):
        self.message,self.code,self.status=message,code,status

def virtual_lot(conn,lot_id):
    row=conn.execute('SELECT * FROM parking_lots WHERE lot_id=%s',(lot_id,)).fetchone()
    if not row:raise SimulationError('주차장을 찾을 수 없습니다.','PARKING_LOT_NOT_FOUND',404)
    if lot_id not in ('C','D') or row['type']!='virtual':raise SimulationError('3·4번 가상 주차장만 시뮬레이션할 수 있습니다.','SIMULATION_REAL_LOT_FORBIDDEN',403)
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('simulation:'+lot_id,))

def enter(conn,lot_id,vehicle_number=None,space_id=None):
    virtual_lot(conn,lot_id)
    if space_id:
        space=conn.execute('SELECT * FROM parking_spaces WHERE space_id=%s AND lot_id=%s FOR UPDATE',(space_id,lot_id)).fetchone()
        if not space:raise SimulationError('선택한 주차칸이 없습니다.','SPACE_NOT_FOUND',404)
        if space['status']!='empty':raise SimulationError('선택한 칸이 비어 있지 않습니다.','SPACE_NOT_EMPTY')
    else:
        space=conn.execute("SELECT * FROM parking_spaces WHERE lot_id=%s AND status='empty' ORDER BY random() FOR UPDATE SKIP LOCKED LIMIT 1",(lot_id,)).fetchone()
        if not space:raise SimulationError('입차할 빈자리가 없습니다.','SIMULATION_LOT_FULL')
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('simulation-vehicles',0))")
    if not vehicle_number:
        available=conn.execute("SELECT v.vehicle_number FROM demo_vehicles v WHERE EXISTS(SELECT 1 FROM parking_sessions s WHERE s.car_id=v.car_id AND s.lot_id IN ('C','D')) AND NOT EXISTS(SELECT 1 FROM parking_sessions s WHERE s.car_id=v.car_id AND ((s.status='active' AND s.exit_at IS NULL) OR s.lot_id IN ('A','B'))) ORDER BY random() LIMIT 1").fetchone()
        if available:vehicle_number=available['vehicle_number']
    if not vehicle_number:
        vehicle_number=conn.execute("SELECT lpad(n::text,4,'0') AS number FROM generate_series(2000,9999) n WHERE NOT EXISTS(SELECT 1 FROM demo_vehicles v WHERE v.vehicle_number=lpad(n::text,4,'0')) ORDER BY n LIMIT 1").fetchone()
        if not vehicle_number:raise SimulationError('사용 가능한 모의 차량번호가 없습니다.','SIMULATION_VEHICLES_EXHAUSTED')
        vehicle_number=vehicle_number['number']
    vehicle=conn.execute('SELECT * FROM demo_vehicles WHERE vehicle_number=%s',(vehicle_number,)).fetchone()
    if vehicle:
        car_id=vehicle['car_id']
        if conn.execute("SELECT 1 FROM parking_sessions WHERE car_id=%s AND lot_id IN ('A','B') LIMIT 1",(car_id,)).fetchone():
            raise SimulationError('실물 주차장 차량은 가상 입차에 사용할 수 없습니다.','SIMULATION_REAL_VEHICLE_FORBIDDEN',403)
        if conn.execute("SELECT 1 FROM parking_sessions WHERE car_id=%s AND status='active' AND exit_at IS NULL LIMIT 1",(car_id,)).fetchone():
            raise SimulationError('이미 주차 중인 차량입니다.','VEHICLE_ALREADY_PARKED')
    else:
        car_id='sim-'+uuid4().hex[:16]
        vehicle=conn.execute('INSERT INTO demo_vehicles(car_id,vehicle_number,plate_number) VALUES(%s,%s,%s) RETURNING *',(car_id,vehicle_number,'88가'+vehicle_number)).fetchone()
    now=datetime.now(KST)
    session=conn.execute("INSERT INTO parking_sessions(session_id,lot_id,space_id,car_id,entry_at,status,is_simulated) VALUES(%s,%s,%s,%s,%s,'active',true) RETURNING *",('sim-session-'+uuid4().hex,lot_id,space['space_id'],car_id,now)).fetchone()
    conn.execute("UPDATE parking_spaces SET status='occupied',car_id=%s WHERE space_id=%s",(car_id,space['space_id']))
    event=conn.execute("INSERT INTO simulation_events(lot_id,action,space_id,car_id,vehicle_number,session_id,occurred_at) VALUES(%s,'entry',%s,%s,%s,%s,%s) RETURNING *",(lot_id,space['space_id'],car_id,vehicle_number,session['session_id'],now)).fetchone()
    return event

def leave(conn,lot_id,space_id=None):
    virtual_lot(conn,lot_id)
    space=conn.execute("SELECT * FROM parking_spaces WHERE lot_id=%s AND status='occupied' AND (%s::text IS NULL OR space_id=%s) ORDER BY random() FOR UPDATE SKIP LOCKED LIMIT 1",(lot_id,space_id,space_id)).fetchone()
    if not space:raise SimulationError('출차할 주차 차량이 없습니다.','SIMULATION_NO_PARKED_VEHICLE')
    session=conn.execute("SELECT * FROM parking_sessions WHERE lot_id=%s AND space_id=%s AND car_id=%s AND status='active' AND exit_at IS NULL ORDER BY entry_at DESC FOR UPDATE LIMIT 1",(lot_id,space['space_id'],space['car_id'])).fetchone()
    if not session:raise SimulationError('주차 세션이 없어 출차하지 않았습니다.','SESSION_NOT_FOUND',404)
    now=datetime.now(KST)
    # PM D-02: active sessions become closed on exit.
    conn.execute("UPDATE parking_sessions SET status='closed',exit_at=%s,is_simulated=true WHERE session_id=%s",(now,session['session_id']))
    conn.execute("UPDATE parking_spaces SET status='empty',car_id=NULL WHERE space_id=%s",(space['space_id'],))
    vehicle=conn.execute('SELECT vehicle_number FROM demo_vehicles WHERE car_id=%s',(space['car_id'],)).fetchone()
    event=conn.execute("INSERT INTO simulation_events(lot_id,action,space_id,car_id,vehicle_number,session_id,occurred_at) VALUES(%s,'exit',%s,%s,%s,%s,%s) RETURNING *",(lot_id,space['space_id'],space['car_id'],vehicle['vehicle_number'] if vehicle else None,session['session_id'],now)).fetchone()
    return event

def tick():
    for lot_id in ('C','D'):
        with connect() as conn:
            state=conn.execute('SELECT * FROM simulation_control WHERE lot_id=%s AND running AND next_tick_at<=now() FOR UPDATE SKIP LOCKED',(lot_id,)).fetchone()
            if not state:continue
            try:
                with conn.transaction():
                    counts=conn.execute("SELECT count(*) FILTER(WHERE status='empty') AS empty,count(*) FILTER(WHERE status='occupied') AS occupied FROM parking_spaces WHERE lot_id=%s",(lot_id,)).fetchone()
                    last=conn.execute('SELECT action FROM simulation_events WHERE lot_id=%s ORDER BY event_id DESC LIMIT 1',(lot_id,)).fetchone()
                    action='entry' if counts['empty'] and (not counts['occupied'] or not last or last['action']=='exit') else 'exit'
                    (enter if action=='entry' else leave)(conn,lot_id)
                conn.execute('UPDATE simulation_control SET next_tick_at=now()+make_interval(secs=>interval_seconds),last_error=NULL WHERE lot_id=%s',(lot_id,))
            except SimulationError as exc:
                conn.execute('UPDATE simulation_control SET next_tick_at=now()+make_interval(secs=>interval_seconds),last_error=%s WHERE lot_id=%s',(exc.message,lot_id))
