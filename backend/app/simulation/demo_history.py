"""Synthetic history for an explicitly enabled demonstration database."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from random import Random

from backend.app.billing.calculator import calculate_parking_fee_pure
from backend.app.simulation.mock_data import SPACES
from backend.app.simulation.vehicle_assignment import REGISTERED_VEHICLES

KST = timezone(timedelta(hours=9))
START = datetime(2025, 1, 1, tzinfo=KST)
END = datetime(2026, 11, 13, tzinfo=KST)
HEARTBEAT = timedelta(seconds=15)
HOUR = timedelta(hours=1)
SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demo_calendar
    (day TEXT PRIMARY KEY, weekday INTEGER, is_weekend INTEGER, is_holiday INTEGER, special_day TEXT);
CREATE TABLE IF NOT EXISTS demo_session_details
    (session_id TEXT PRIMARY KEY REFERENCES parking_sessions, vehicle_type TEXT, virtual_plate TEXT);
CREATE TABLE IF NOT EXISTS demo_observation_history
    (session_id TEXT REFERENCES parking_sessions, event_type TEXT, observed_at TEXT, PRIMARY KEY(session_id,event_type));
CREATE TABLE IF NOT EXISTS demo_airport_reservations
    (reservation_id TEXT PRIMARY KEY, session_id TEXT REFERENCES parking_sessions, zone TEXT,
     created_at TEXT, start_at TEXT, end_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS demo_hourly_usage
    (lot_id TEXT REFERENCES parking_lots, hour_kst TEXT, entries INTEGER, exits INTEGER,
     occupied_seconds INTEGER, capacity_seconds INTEGER, PRIMARY KEY(lot_id,hour_kst));
CREATE TABLE IF NOT EXISTS demo_outages
    (start_at TEXT PRIMARY KEY, end_at TEXT NOT NULL, running_lots TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_demo_sim_session ON simulation_movements(session_id, action);
CREATE INDEX IF NOT EXISTS ix_demo_payment_session ON mock_payments(session_id);
CREATE INDEX IF NOT EXISTS ix_demo_session_exit ON parking_sessions(lot_id, julianday(exit_at));
"""
HOLIDAYS = {
    2025: '01-01 01-27 01-28 01-29 01-30 03-01 03-03 05-05 05-06 06-03 06-06 08-15 10-03 10-05 10-06 10-07 10-08 10-09 12-25',
    2026: '01-01 02-16 02-17 02-18 03-01 03-02 05-01 05-05 05-24 05-25 06-03 06-06 07-17 08-15 08-17 09-24 09-25 09-26 10-03 10-05 10-09 12-25',
}
SPECIAL = {'05-08': '어버이날', '07-31': '휴가철', '08-01': '휴가철',
           '11-11': '쇼핑 행사 가정', '12-24': '크리스마스이브', '12-31': '연말'}
PROFILES = {
    'A': (32, (8, 9, 12, 14, 17), (35, 24, 12, 18, 11), 110, .27, .18),
    'B': (59, (6, 8, 9, 12, 14, 17, 20, 22), (12, 20, 8, 14, 13, 21, 5, 5), 105, 1.21, 1.39),
    'C': (92, (5, 7, 9, 12, 14, 16, 18, 21), (15, 19, 13, 16, 9, 9, 16, 12), 180, 1.22, 1.50),
    'D': (155, (10, 11, 12, 13, 15, 16, 18, 20, 22), (7, 16, 8, 21, 17, 8, 19, 10, 5), 100, 1.49, 1.36),
}


def local_time(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=KST)).astimezone(KST)


def metadata(conn, key):
    row = conn.execute("SELECT value FROM demo_metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def save(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO demo_metadata VALUES (?,?)", (key, value))


def day_type(day):
    key = day.strftime('%m-%d')
    return day.weekday(), day.weekday() >= 5, key in HOLIDAYS.get(day.year, '').split(), SPECIAL.get(key)


def virtual_plate(rng):
    low, high = rng.choice(((10, 69), (100, 699)))
    letter = rng.choice("가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주")
    return f"{rng.randint(low, high)}{letter}{rng.randint(101, 9999):04d}"


def save_mock_payment(conn, session_id, lot, car, entered, exited, policy, *, canceled_at=None):
    amount = calculate_parking_fee_pure(entered, exited, policy)['amount_krw']
    payment = f'{session_id}-auto-payment'
    conn.execute(
        """INSERT INTO mock_payments VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (payment, session_id, lot, car, amount, 'MOCK_CARD',
         'canceled' if canceled_at else 'completed', payment, exited, canceled_at,
         '시연용 모의 취소' if canceled_at else None),
    )
    conn.execute("INSERT INTO audit_logs VALUES (?,?,?,?,?)",
                 (payment, 'demo:synthetic', 'MOCK_PAYMENT', payment, exited))


def generate_window(conn, start, end, lots, *, historical=False):
    if start >= end or not lots:
        return
    rng = Random(f'parkon-20261112/{start.isoformat()}')
    prefix = 'demo-seed' if historical else f'gap-{start.isoformat()}'
    policies = {row['lot_id']: dict(row) for row in conn.execute("SELECT * FROM rate_policies")}
    bays = {(lot, kind): [s.space_id for s in SPACES if s.lot_id == lot and s.space_type == kind]
            for lot in lots for kind in ('general', 'ev', 'accessible')}
    busy = {}
    if not historical:
        # Reserve the original bays throughout the outage for sessions that cross it.
        busy = {(row['lot_id'], row['space_id']): end for row in conn.execute(
            """SELECT lot_id, space_id FROM parking_sessions
               WHERE lot_id IN ('C','D') AND julianday(entry_at)<julianday(?)
                 AND (exit_at IS NULL OR julianday(exit_at)>julianday(?))
                 AND session_id NOT GLOB 'demo-*'""", (end.isoformat(), start.isoformat())
        )}
    sequence = 0
    hourly = defaultdict(lambda: [0, 0, 0])
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        weekday, weekend, holiday, special = day_type(day.date())
        conn.execute("INSERT OR IGNORE INTO demo_calendar VALUES (?,?,?,?,?)",
                     (day.date().isoformat(), weekday, weekend, holiday, special))
        for lot in lots:
            volume, hours, weights, dwell, weekend_factor, holiday_factor = PROFILES[lot]
            factor = holiday_factor if holiday else weekend_factor if weekend else 1
            if special:
                factor *= .88 if lot == 'A' else 1.2
            if lot in 'BC' and day.month in (7, 8):
                factor *= 1.12
            count = round(volume * factor * rng.uniform(.78, 1.23))
            arrivals = sorted(day + timedelta(hours=hour, seconds=rng.randrange(3600))
                              for hour in rng.choices(hours, weights=weights, k=count))
            for entry in arrivals:
                available_minutes = int((end - entry - timedelta(seconds=1)).total_seconds() // 60)
                if entry < start or available_minutes < 9:
                    continue
                kind = rng.choices(('general', 'ev', 'accessible'), weights=(86, 11, 3))[0]
                free = [bay for bay in bays[lot, kind] if busy.get((lot, bay), start) <= entry]
                if not free:
                    continue
                bay = rng.choice(free)
                minutes = max(9, round(rng.lognormvariate(0, .60) * dwell))
                if lot == 'C' and rng.random() < .26:
                    minutes += rng.randint(720, 4200)
                if minutes > available_minutes:
                    minutes = rng.randint(9, available_minutes)
                exit_at = entry + timedelta(minutes=minutes)
                busy[lot, bay] = exit_at
                sequence += 1
                sid = f'{prefix}-{sequence:07d}'
                car = str(rng.randrange(10, 100)) if lot in 'AB' else None
                plate = virtual_plate(rng)
                entered, exited = entry.isoformat(), exit_at.isoformat()
                conn.execute("INSERT INTO parking_sessions VALUES (?,?,?,?,?,?,?)",
                             (sid, lot, bay, car, entered, exited, 'closed'))
                conn.execute("INSERT INTO demo_session_details VALUES (?,?,?)", (sid, kind, plate))
                conn.execute("INSERT INTO session_vehicle_plates VALUES (?,?)", (sid, plate))
                conn.executemany("INSERT INTO demo_observation_history VALUES (?,?,?)",
                                 [(sid, 'vehicle_observed', entered), (sid, 'space_empty_observed', exited)])
                if historical and lot == 'C' and rng.random() < .32:
                    zone = 'C' if exit_at - entry >= timedelta(days=1) else 'A'
                    conn.execute("INSERT INTO demo_airport_reservations VALUES (?,?,?,?,?,?,?)",
                                 (f'{sid}-reservation', sid, zone, (entry - timedelta(days=2)).isoformat(),
                                  entered, exited, 'completed'))
                if rng.random() < .92:
                    canceled = historical and rng.random() < .023 and exit_at + timedelta(minutes=5) < end
                    canceled_at = (exit_at + timedelta(minutes=5)).isoformat() if canceled else None
                    save_mock_payment(conn, sid, lot, car, entered, exited, policies[lot], canceled_at=canceled_at)
                hour = entry.replace(minute=0, second=0, microsecond=0)
                hourly[lot, hour][0] += 1
                hourly[lot, exit_at.replace(minute=0, second=0, microsecond=0)][1] += 1
                while hour < exit_at:
                    hourly[lot, hour][2] += (min(hour + HOUR, exit_at) - max(hour, entry)).total_seconds()
                    hour += HOUR
            if historical and (day.date() - START.date()).days % 29 == 'ABCD'.index(lot) * 4:
                detected = day + timedelta(hours=14, minutes=rng.randrange(60))
                resolved = detected + timedelta(minutes=8)
                if resolved < end:
                    alert_id = f'demo-alert-{lot}-{day.date()}'
                    conn.execute(
                        """INSERT INTO alerts (alert_id,lot_id,type,level,status,source,message,confidence,
                                               detected_at,acknowledged_at,resolved_at,actor)
                           VALUES (?,?, 'mock_fire_led',2,'resolved','simulation',?,.99,?,?,?,?)""",
                        (alert_id, lot, '시연용 LED 모의 경보', detected.isoformat(),
                         (detected + timedelta(minutes=2)).isoformat(), resolved.isoformat(), 'demo:synthetic'),
                    )
                    conn.execute("INSERT INTO alert_events VALUES (?,?,?)",
                                 (f'{alert_id}-event', alert_id, detected.isoformat()))
        day += timedelta(days=1)
    sizes = dict(conn.execute("SELECT lot_id,total_spaces FROM parking_lots"))
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour < end:
        seconds = (min(hour + HOUR, end) - max(hour, start)).total_seconds()
        conn.executemany(
            """INSERT INTO demo_hourly_usage VALUES (?,?,?,?,?,?)
               ON CONFLICT(lot_id,hour_kst) DO UPDATE SET entries=entries+excluded.entries,
                 exits=exits+excluded.exits, occupied_seconds=occupied_seconds+excluded.occupied_seconds,
                 capacity_seconds=capacity_seconds+excluded.capacity_seconds""",
            [(lot, hour.isoformat(), *hourly[lot, hour], sizes[lot] * seconds) for lot in lots],
        )
        hour += HOUR


REFILL_BOUNDS = (.15, .85)
REFILL_FALLBACK = .5


def typical_occupancy(conn, lot, now):
    """Average occupancy of the lot for this hour on the same kind of day (weekday vs weekend/holiday)."""
    weekday, weekend, holiday, _ = day_type(now.date())
    row = conn.execute(
        """SELECT sum(u.occupied_seconds) * 1.0 / nullif(sum(u.capacity_seconds), 0)
           FROM demo_hourly_usage u JOIN demo_calendar c ON c.day = substr(u.hour_kst, 1, 10)
           WHERE u.lot_id = ? AND substr(u.hour_kst, 12, 2) = ?
             AND (c.is_weekend OR c.is_holiday) = ?""",
        (lot, f'{now.hour:02d}', int(bool(weekend or holiday))),
    ).fetchone()
    ratio = row[0] if row and row[0] is not None else REFILL_FALLBACK
    return min(max(ratio, REFILL_BOUNDS[0]), REFILL_BOUNDS[1])


def refill(conn, now, lots):
    """Start running virtual lots from a fresh, typical occupancy instead of the pre-restart cars.

    Vehicles left from before the restart are released (occupied=0); the observation loop then reports
    their bays empty, so the server closes those sessions through the normal pipeline and checkpoint()
    settles them. New vehicles are placed only in bays without an active session, and they become
    sessions only when the server confirms the simulated observations (rule: server decides state).
    """
    rng = Random(f'parkon-refill/{now.isoformat()}')
    used = {row[0] for row in conn.execute(
        "SELECT vehicle_number FROM simulation_targets WHERE occupied=1 AND lot_id NOT IN (%s)"
        % ','.join('?' * len(lots)), tuple(lots)) if row[0]} if lots else set()
    pool = [f'{value:04d}' for value in range(101, 10000)
            if f'{value:04d}' not in used and f'{value:04d}' not in REGISTERED_VEHICLES]
    rng.shuffle(pool)
    placed = {}
    for lot in lots:
        conn.execute("UPDATE simulation_targets SET occupied=0 WHERE lot_id=?", (lot,))
        held = {row[0] for row in conn.execute(
            "SELECT space_id FROM parking_sessions WHERE lot_id=? AND status='active'", (lot,))}
        bays = [s for s in SPACES if s.lot_id == lot and s.space_id not in held]
        total = sum(1 for s in SPACES if s.lot_id == lot)
        count = min(len(bays), round(typical_occupancy(conn, lot, now) * total))
        chosen = rng.sample(bays, count)
        for bay in chosen:
            number = pool.pop()
            low, high = rng.choice(((10, 69), (100, 699)))
            letter = rng.choice("가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주")
            conn.execute(
                "UPDATE simulation_targets SET occupied=1, vehicle_number=?, plate_number=?, vehicle_type=?"
                " WHERE lot_id=? AND space_id=?",
                (number, f'{rng.randint(low, high)}{letter}{number}', bay.space_type, lot, bay.space_id),
            )
        placed[lot] = count
    if placed:
        save(conn, 'last_refill_kst', now.isoformat())
    return placed


def prepare(conn, now):
    now = local_time(now)
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = metadata(conn, 'runtime_heartbeat_kst')
        if previous is None:
            if metadata(conn, 'start_kst') is None:
                if conn.execute("SELECT 1 FROM parking_sessions LIMIT 1").fetchone():
                    raise RuntimeError("Continuous demo requires a new database or a seeded demo database")
                cutover = min(now, END)
                generate_window(conn, START, cutover, 'ABCD', historical=True)
                save(conn, 'source', 'synthetic demonstration only')
                save(conn, 'start_kst', START.isoformat())
                save(conn, 'visible_through_kst', cutover.isoformat())
                save(conn, 'end_exclusive_kst', END.isoformat())
                previous = cutover.isoformat()
            else:
                existing_cutoff = metadata(conn, 'visible_through_kst')
                cutoff = min(now, END, local_time(existing_cutoff)) if existing_cutoff else min(now, END)
                save(conn, 'visible_through_kst', cutoff.isoformat())
            save(conn, 'runtime_started_kst', (previous or now.isoformat()))
            conn.execute("UPDATE simulation_controls SET running=1,interval_seconds=30,next_tick=?",
                         (now.timestamp(),))
            if metadata(conn, 'last_refill_kst') is None:
                refill(conn, now, 'CD')
        if previous and local_time(previous) < now:
            lots = ''.join(row['lot_id'] for row in conn.execute(
                "SELECT lot_id FROM simulation_controls WHERE running=1 ORDER BY lot_id"))
            start = local_time(previous)
            generate_window(conn, start, now, lots)
            conn.execute("INSERT INTO demo_outages VALUES (?,?,?)", (previous, now.isoformat(), lots))
            # Restarted virtual lots begin from a typical occupancy for this hour, not the old cars.
            refill(conn, now, lots)
        save(conn, 'runtime_heartbeat_kst', max(now, local_time(previous)).isoformat() if previous else now.isoformat())


def checkpoint(conn, now):
    """Settle only observed simulated departures, once per session, and persist the heartbeat."""
    now = local_time(now)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        start = metadata(conn, 'runtime_started_kst')
        rows = conn.execute(
            """SELECT s.*, p.* FROM parking_sessions s JOIN rate_policies p USING(lot_id)
               WHERE s.lot_id IN ('C','D') AND s.status='closed'
                 AND julianday(s.exit_at)>=julianday(?)
                 AND EXISTS (SELECT 1 FROM simulation_movements m
                             WHERE m.session_id=s.session_id AND m.action='exit')
                 AND s.session_id NOT GLOB 'demo-*'
                 AND NOT EXISTS (SELECT 1 FROM mock_payments m WHERE m.session_id=s.session_id)""",
            (start,),
        ).fetchall()
        for row in rows:
            save_mock_payment(conn, row['session_id'], row['lot_id'], row['car_id'],
                              row['entry_at'], row['exit_at'], dict(row))
        previous = local_time(metadata(conn, 'runtime_heartbeat_kst'))
        save(conn, 'runtime_heartbeat_kst', max(now, previous).isoformat())


def monthly_usage(conn, month, lot, cutoff, heartbeat):
    try:
        month_start = datetime.fromisoformat(f'{month}-01').replace(tzinfo=KST)
        month_end = (month_start + timedelta(days=32)).replace(day=1)
    except (ValueError, OverflowError):
        return [], None
    observed_until = local_time(heartbeat)
    start = max(month_start, START)
    end = min(month_end, observed_until)
    lots = {row['lot_id']: row['total_spaces'] for row in conn.execute(
        "SELECT lot_id, total_spaces FROM parking_lots WHERE ? IS NULL OR lot_id=?", (lot, lot)
    )}
    outages = conn.execute(
        """SELECT * FROM demo_outages WHERE julianday(start_at)<julianday(?)
             AND julianday(end_at)>julianday(?)""", (end.isoformat(), start.isoformat())
    ).fetchall()
    uncovered = {lot_id: [(local_time(row['start_at']), local_time(row['end_at']))
                           for row in outages if lot_id not in row['running_lots']]
                 for lot_id in lots}
    bins = {}
    hour = start
    while hour < end:
        next_hour = min(hour + HOUR, end)
        day = hour.date()
        weekday, weekend, holiday, _ = day_type(day)
        for lot_id, capacity in lots.items():
            coverage = covered_seconds(hour, next_hour, uncovered[lot_id])
            if coverage and capacity:
                bins[lot_id, hour] = {
                    'lot_id': lot_id, 'day': day.isoformat(), 'hour': hour.strftime('%H'),
                    'weekday': weekday, 'is_weekend': weekend, 'is_holiday': holiday,
                    'entries': 0, 'occupied': 0, 'capacity': coverage * capacity,
                }
        hour += HOUR
    durations = []
    rows = conn.execute(
        """SELECT lot_id, entry_at, exit_at FROM parking_sessions
           WHERE (? IS NULL OR lot_id=?) AND julianday(entry_at)<julianday(?)
             AND (exit_at IS NULL OR julianday(exit_at)>julianday(?))
             AND (? IS NULL OR session_id NOT GLOB 'demo-*' OR julianday(exit_at)<=julianday(?))""",
        (lot, lot, end.isoformat(), start.isoformat(), cutoff, cutoff),
    )
    for row in rows:
        lot_id = row['lot_id']
        if lot_id not in lots:
            continue
        entered = local_time(row['entry_at'])
        exited = local_time(row['exit_at']) if row['exit_at'] else end
        if exited <= entered:
            continue
        if start <= entered < end:
            entry_bin = bins.get((lot_id, entered.replace(minute=0, second=0, microsecond=0)))
            if entry_bin and not any(a <= entered < b for a, b in uncovered[lot_id]):
                entry_bin['entries'] += 1
            if row['exit_at'] and exited <= observed_until:
                durations.append((exited - entered).total_seconds())
        hour = max(entered, start).replace(minute=0, second=0, microsecond=0)
        while hour < min(exited, end):
            bucket = bins.get((lot_id, hour))
            if bucket:
                bucket['occupied'] += covered_seconds(max(hour, entered), min(hour + HOUR, exited, end),
                                                      uncovered[lot_id])
            hour += HOUR
    for bucket in bins.values():
        bucket['occupied'] = min(bucket['occupied'], bucket['capacity'])
    return list(bins.values()), {
        'average': sum(durations) / (60 * len(durations)) if durations else None,
        'short': sum(d < 3600 for d in durations),
        'medium': sum(3600 <= d < 10800 for d in durations),
        'long': sum(10800 <= d < 86400 for d in durations),
        'overnight': sum(d >= 86400 for d in durations),
    }


def covered_seconds(start, end, outages):
    return (end - start).total_seconds() - sum(
        max(0, (min(end, stopped_until) - max(start, stopped_at)).total_seconds())
        for stopped_at, stopped_until in outages
    )
