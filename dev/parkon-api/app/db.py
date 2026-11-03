import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .billing import POLICIES
from .layouts import LAYOUTS

def connect():
    return psycopg.connect(host=os.getenv('PGHOST', 'db'), dbname=os.getenv('PGDATABASE', 'parkon'), user=os.getenv('PGUSER', 'parkon'), password=os.environ['PGPASSWORD'], row_factory=dict_row)

def initialize():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(441102)')
        conn.execute(Path(__file__).with_name('schema.sql').read_text())
        # One-time renumbering of legacy demo fixtures. Session IDs and payment amounts stay intact.
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=7').fetchone():
            if conn.execute("SELECT 1 FROM demo_vehicles WHERE car_id BETWEEN '01' AND '09' LIMIT 1").fetchone():
                if conn.execute("SELECT 1 FROM demo_vehicles WHERE car_id BETWEEN '25' AND '33' LIMIT 1").fetchone():
                    raise RuntimeError('Sticker migration conflicts with custom IDs 25..33; no changes committed.')
                for table in ('demo_vehicles','parking_spaces','parking_sessions','mock_payments'):
                    conn.execute(f"UPDATE {table} SET car_id='legacy-sticker-'||car_id WHERE car_id ~ '^[0-9]{{2}}$' AND car_id BETWEEN '01' AND '24'")
                    conn.execute(f"UPDATE {table} SET car_id=lpad((substring(car_id from 16)::integer+9)::text,2,'0') WHERE car_id ~ '^legacy-sticker-[0-9]{{2}}$'")
            conn.execute('INSERT INTO schema_migrations(version) VALUES(7)')
        # Vehicle plates remain demo-only; physical sticker IDs start at 10.

        vehicle_numbers = ['1001','1002','1003','1004','1005','1006','1234','1008',
                           '1009','1010','1011','5678','1013','1014','9012','1016',
                           '1017','1018','3456','1020','1021','0022','1023','1024']
        plate_prefixes = ['11가','23나','35다','47라','59마','61거','12가','24너',
                          '36더','48러','50머','34나','62고','74노','56다','86도',
                          '98로','20모','78라','32구','44누','90마','66두','88루']
        for car_id, vehicle_number, prefix in zip((f'{n:02}' for n in range(10,34)), vehicle_numbers, plate_prefixes):
            conn.execute('INSERT INTO demo_vehicles(car_id,vehicle_number,plate_number) VALUES(%s,%s,%s) ON CONFLICT(car_id) DO UPDATE SET plate_number=COALESCE(demo_vehicles.plate_number,EXCLUDED.plate_number)', (car_id,vehicle_number,prefix+vehicle_number))
        number = 10
        occupied = [[0,1,3,5,8,10,11], [0,2,4,7,9,11], [1,3,6,8,9], [0,4,8,9]]
        for index, lot_id in enumerate('ABCD'):
            layout = LAYOUTS[lot_id]
            capacity = layout['capacity']
            kind = 'real' if index < 2 else 'virtual'
            name = layout['name']
            conn.execute('INSERT INTO parking_lots(lot_id,name,type,layout_type) VALUES(%s,%s,%s,%s) ON CONFLICT(lot_id) DO UPDATE SET name=EXCLUDED.name,type=EXCLUDED.type,layout_type=EXCLUDED.layout_type', (lot_id,name,kind,layout['layout_type']))
            conn.execute('INSERT INTO rate_policies VALUES(%s,%s) ON CONFLICT DO NOTHING', (lot_id,Jsonb(POLICIES[lot_id])))
            for i in range(capacity):
                sid = f'{lot_id}-{i+1:02}'
                car = f'{number:02}' if i in occupied[index] else None
                if car: number += 1
                status = 'occupied' if car else 'unavailable' if index==1 and i==5 else 'uncertain' if (index==1 and i==6) or (index==0 and i==6) else 'empty'
                space_type = 'ev' if i in layout['ev'] else 'accessible' if i in layout['accessible'] else 'general'
                zone = 'A' if i < layout['bank'] else 'B'
                bay_label = f"{zone}-{i % layout['bank'] + 1:02}"
                # Refresh drawing metadata only; preserve occupancy, sessions and payment history.
                conn.execute('INSERT INTO parking_spaces(space_id,lot_id,zone,space_type,status,car_id,bay_label) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(space_id) DO UPDATE SET zone=EXCLUDED.zone,space_type=EXCLUDED.space_type,bay_label=EXCLUDED.bay_label', (sid,lot_id,zone,space_type,status,car,bay_label))
                if car:
                    conn.execute("INSERT INTO parking_sessions(session_id,lot_id,space_id,car_id,entry_at) SELECT %s,%s,%s,%s,%s WHERE NOT EXISTS(SELECT 1 FROM parking_sessions WHERE car_id=%s AND lot_id=%s AND status='parked') ON CONFLICT DO NOTHING", ('s-'+car,lot_id,sid,car,'2026-11-02T12:40:00+09:00',car,lot_id))
        for pid,lot,car,amount,when in [('seed-01','A','32',1000,'2026-11-02T13:05:00+09:00'),('seed-02','B','33',1200,'2026-11-02T13:20:00+09:00'),('seed-03','C','32',2600,'2026-11-01T11:40:00+09:00'),('seed-04','D','33',3200,'2026-10-31T15:00:00+09:00')]:
            conn.execute("INSERT INTO mock_payments(payment_id,session_id,lot_id,car_id,amount_krw,status,idempotency_key,created_at) VALUES(%s,%s,%s,%s,%s,'completed',%s,%s) ON CONFLICT DO NOTHING", (pid,'closed-'+pid,lot,car,amount,pid,when))
