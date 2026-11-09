"""Seeded hourly entry/exit simulation. Never writes live occupancy or real history."""
from datetime import timedelta
from math import exp
from random import Random
from backend.app.forecasting import KST

VERSION = 'hourly-arrivals-v1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS demo_congestion_history (
 version TEXT NOT NULL, lot_id TEXT NOT NULL, bucket TEXT NOT NULL,
 capacity INTEGER NOT NULL, empty INTEGER NOT NULL, arrivals INTEGER NOT NULL,
 departures INTEGER NOT NULL, PRIMARY KEY(version,lot_id,bucket));'''


def simulate(lot, start, days=70, capacity=64):
    """Airport morning/evening demand, mall afternoon/weekend shopping demand."""
    if lot not in ('C','D'):raise ValueError('Virtual lots only')
    rng=Random(f'{VERSION}:{lot}:{start.date()}')
    occupied=round(capacity*.25)
    rows=[]
    for index in range(days*24):
        at=start+timedelta(hours=index)
        h=at.hour;weekend=at.weekday()>=5
        if lot=='C':
            demand=.26+.38*exp(-((h-9)/3.5)**2)+.28*exp(-((h-18)/3)**2)+(.10 if weekend else 0)
        else:
            demand=.04+(.83 if weekend else .59)*exp(-((h-15)/4.2)**2)
        target=round(capacity*min(.96,max(.02,demand+rng.gauss(0,.045))))
        turnover=min(occupied,rng.randint(0,5))
        departures=min(occupied,max(0,occupied-target)+turnover)
        arrivals=min(capacity-occupied+departures,max(0,target-occupied)+turnover)
        occupied+=arrivals-departures
        rows.append(dict(lot_id=lot,bucket=at.isoformat(),capacity=capacity,empty=capacity-occupied,
                         arrivals=arrivals,departures=departures,reliable=1,source=VERSION,is_mock=1))
    return rows


def ensure_history(conn, now):
    """Reproducible historical scenario; future target observations are never seeded."""
    conn.executescript(SCHEMA)
    today=now.astimezone(KST).replace(hour=0,minute=0,second=0,microsecond=0)
    # Fixed origin preserves a stable scenario across restarts/dates.
    from datetime import datetime
    origin=datetime(2026,8,1,tzinfo=KST)
    if today<=origin:return
    days=(today-origin).days
    for lot in ('C','D'):
        last=conn.execute('SELECT max(bucket) FROM demo_congestion_history WHERE version=? AND lot_id=?',(VERSION,lot)).fetchone()[0]
        if last and last>=(today-timedelta(hours=1)).isoformat():continue
        rows=simulate(lot,origin,days)
        conn.executemany('INSERT OR IGNORE INTO demo_congestion_history VALUES(?,?,?,?,?,?,?)',
                         [(VERSION,lot,r['bucket'],r['capacity'],r['empty'],r['arrivals'],r['departures']) for r in rows])


def history(conn,lot,now):
    rows=conn.execute('SELECT * FROM demo_congestion_history WHERE version=? AND lot_id=? AND bucket<? ORDER BY bucket',
                      (VERSION,lot,now.astimezone(KST).isoformat())).fetchall()
    return [{**dict(r),'reliable':1,'source':VERSION,'is_mock':1} for r in rows]
