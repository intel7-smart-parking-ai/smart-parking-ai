"""Read-only experimental forecast over isolated simulation records."""
from datetime import datetime, timedelta
from statistics import mean
from backend.app.forecasting import KST, backtest


def forecast(records, target, now):
    now=now.astimezone(KST);target=target.astimezone(KST)
    if target<=now or target>now+timedelta(days=7):
        raise ValueError('지금 이후부터 7일 이내의 시각을 선택해 주세요.')
    train=[r for r in records if datetime.fromisoformat(r['bucket'])<now and r.get('is_mock') and r.get('reliable')]
    validation=backtest(train)
    if validation['status']!='validated_baselines':raise ValueError('시연 기록이 부족해 예측할 수 없어요.')
    # Empirical error below is a holdout MAE, not a calibrated prediction interval.
    error=round(validation['metrics']['weekday_hour_mean']['mae_spaces'],1)
    capacity=train[-1]['capacity'];items=[]
    day=target.replace(hour=0,minute=0,second=0,microsecond=0)
    for hour in range(24):
        at=day+timedelta(hours=hour)
        if at<=now:continue
        peers=[r['empty'] for r in train if (t:=datetime.fromisoformat(r['bucket'])).weekday()==at.weekday() and t.hour==hour]
        if not peers:continue
        empty=min(capacity,max(0,round(mean(peers))))
        occupied=1-empty/capacity
        items.append({'at':at.isoformat(),'empty':empty,'capacity':capacity,
                      'level':'혼잡' if occupied>=.85 else '보통' if occupied>=.6 else '여유'})
    selected=next((r for r in items if datetime.fromisoformat(r['at']).hour==target.hour),None)
    # For this hour's remaining minutes, use the hourly bucket estimate.
    if selected is None:
        peers=[r['empty'] for r in train if (t:=datetime.fromisoformat(r['bucket'])).weekday()==target.weekday() and t.hour==target.hour]
        if not peers:raise ValueError('해당 시간대의 기록이 부족해요.')
        empty=round(mean(peers));occupied=1-empty/capacity
        selected={'at':target.isoformat(),'empty':empty,'capacity':capacity,'level':'혼잡' if occupied>=.85 else '보통' if occupied>=.6 else '여유'}
        items.insert(0,selected)
    return {'lot_id':train[0]['lot_id'],'target':target.isoformat(),'selected':selected,'hours':items,
            'is_mock':True,'source':'simulation','model':'weekday_hour_mean','model_version':'hourly-arrivals-v1',
            'label':'시뮬레이션 기반 예측','mae_spaces':error,'validation_samples':validation['metrics']['weekday_hour_mean']['n'],
            'generated_at':now.isoformat(),'training_end':train[-1]['bucket']}
