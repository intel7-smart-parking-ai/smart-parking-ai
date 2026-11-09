"""Anonymous hourly history and chronological next-day baseline validation."""
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import mean

KST = timezone(timedelta(hours=9))
SCHEMA = '''CREATE TABLE IF NOT EXISTS vacancy_history (
 lot_id TEXT NOT NULL, bucket TEXT NOT NULL, collected_at TEXT NOT NULL,
 capacity INTEGER NOT NULL, empty INTEGER NOT NULL, reliable INTEGER NOT NULL,
 source TEXT NOT NULL, is_mock INTEGER NOT NULL, PRIMARY KEY(lot_id,bucket));'''


def record(conn, lot, spaces, now, is_mock=False):
    bucket=now.astimezone(KST).replace(minute=0,second=0,microsecond=0).isoformat()
    reliable=bool(spaces) and all(r.get('observed_at') is not None and r['status'] in ('empty','occupied') for r in spaces)
    source='simulation' if is_mock else 'vision' if reliable else 'unobserved'
    conn.execute('''INSERT INTO vacancy_history VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(lot_id,bucket) DO UPDATE SET collected_at=excluded.collected_at,
      capacity=excluded.capacity,empty=excluded.empty,reliable=excluded.reliable,
      source=excluded.source,is_mock=excluded.is_mock''',
      (lot,bucket,now.isoformat(),len(spaces),sum(r['status']=='empty' for r in spaces),int(reliable),source,int(is_mock)))


def predict_day(series, day):
    train={t:v for t,v in series.items() if t<day}
    predictions={}
    for hour in range(24):
        target=day+timedelta(hours=hour)
        previous_day=target-timedelta(days=1)
        previous_week=target-timedelta(days=7)
        peers=[v for t,v in train.items() if t.weekday()==target.weekday() and t.hour==target.hour]
        if previous_day in train and previous_week in train and peers:
            predictions[target]=(train[previous_day],train[previous_week],mean(peers))
    return predictions


def backtest(records, min_train_days=28, test_days=14):
    rows=[r for r in records if r.get('reliable') and r['capacity']>0 and 0<=r['empty']<=r['capacity']]
    if not rows:
        return {'status':'insufficient_data','usable_hours':0,'required_days':min_train_days+test_days}
    if any(len({r[key] for r in rows})!=1 for key in ('lot_id','source','is_mock','capacity')):
        return {'status':'incompatible_data','reason':'Do not mix lots, sources, modes or capacities.'}
    series={datetime.fromisoformat(r['bucket']).astimezone(KST):r['empty'] for r in rows}
    first,last=min(series),max(series)
    end=last.replace(hour=0)
    start=end-timedelta(days=test_days)
    required_start=start-timedelta(days=min_train_days)
    coverage=sum(required_start<=t<end for t in series)/((min_train_days+test_days)*24)
    if first>required_start or coverage<0.9:
        return {'status':'insufficient_data','usable_hours':len(series),'coverage':round(coverage,4),
                'required_days':min_train_days+test_days,'is_mock':bool(rows[0]['is_mock'])}
    errors={name:[] for name in ('previous_day','previous_week','weekday_hour_mean')}
    day=start
    while day<end:
        for target,predictions in predict_day(series,day).items():
            if target not in series:
                continue
            for name,pred in zip(errors,predictions):
                errors[name].append(pred-series[target])
        day+=timedelta(days=1)
    if len(errors['previous_day'])<test_days*24*0.9:
        return {'status':'insufficient_data','reason':'Too few common evaluation targets.'}
    metrics={name:{'mae_spaces':round(mean(abs(e) for e in es),3),
                   'rmse_spaces':round(sqrt(mean(e*e for e in es)),3),'n':len(es)} for name,es in errors.items()}
    return {'status':'validated_baselines','is_mock':bool(rows[0]['is_mock']),
            'evaluation_start':start.isoformat(),'evaluation_end_exclusive':end.isoformat(),
            'horizon':'next day, hourly, rolling daily origins','metrics':metrics,
            'beats_baselines':metrics['weekday_hour_mean']['mae_spaces']<min(metrics[n]['mae_spaces'] for n in ('previous_day','previous_week')),
            'production_eligible':False,
            'note':'Baseline experiment only; synthetic validation does not establish real accuracy.'}
