import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .billing import POLICIES

def connect():
    return psycopg.connect(host=os.getenv('PGHOST', 'db'), dbname=os.getenv('PGDATABASE', 'parkon'), user=os.getenv('PGUSER', 'parkon'), password=os.environ['PGPASSWORD'], row_factory=dict_row)

def initialize():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(441102)')
        conn.execute(Path(__file__).with_name('schema.sql').read_text())
        number = 1
        occupied = [[0,1,3,5,8,10,11], [0,2,4,7,9,11], [1,3,6,8,9], [0,4,8,9]]
        for index, lot_id in enumerate('ABCD'):
            capacity = 12 if index < 2 else 10
            kind = 'real' if index < 2 else 'virtual'
            name = f"{'실물' if index < 2 else '가상'} {lot_id} 주차장"
            conn.execute('INSERT INTO parking_lots VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (lot_id,name,kind))
            conn.execute('INSERT INTO rate_policies VALUES(%s,%s) ON CONFLICT DO NOTHING', (lot_id,Jsonb(POLICIES[lot_id])))
            for i in range(capacity):
                sid = f'{lot_id}-{i+1:02}'
                car = f'{number:02}' if i in occupied[index] else None
                if car: number += 1
                status = 'occupied' if car else 'unavailable' if index==1 and i==5 else 'uncertain' if (index==1 and i==6) or (index==0 and i==6) else 'empty'
                space_type = 'ev' if i in [capacity-3,capacity-2] else 'accessible' if i==capacity-1 else 'general'
                conn.execute('INSERT INTO parking_spaces VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (sid,lot_id,'A' if i<capacity/2 else 'B',space_type,status,car))
                if car:
                    conn.execute("INSERT INTO parking_sessions(session_id,lot_id,space_id,car_id,entry_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", ('s-'+car,lot_id,sid,car,'2026-11-02T12:40:00+09:00'))
        for pid,lot,car,amount,when in [('seed-01','A','23',1000,'2026-11-02T13:05:00+09:00'),('seed-02','B','24',1200,'2026-11-02T13:20:00+09:00'),('seed-03','C','23',2600,'2026-11-01T11:40:00+09:00'),('seed-04','D','24',3200,'2026-10-31T15:00:00+09:00')]:
            conn.execute("INSERT INTO mock_payments(payment_id,session_id,lot_id,car_id,amount_krw,status,idempotency_key,created_at) VALUES(%s,%s,%s,%s,%s,'completed',%s,%s) ON CONFLICT DO NOTHING", (pid,'closed-'+pid,lot,car,amount,pid,when))
