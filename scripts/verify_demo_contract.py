"""Read-only checks for the PM sticker rule and the demo HTTP contract."""
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
BASE='http://127.0.0.1:8002/api/v1'
def call(path,method='GET',body=None):
    req=Request(BASE+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=15) as response:return response.status,json.load(response)
    except HTTPError as exc:return exc.code,json.load(exc)
def collection(path):
    code,data=call(path)
    assert code==200 and set(data)=={'items','next_cursor','as_of'},(path,code)
    assert isinstance(data['items'],list) and data['next_cursor'] is None and data['as_of']
    return data['items']
lots=collection('/parking-lots')
for lot in lots:
    spaces=collection('/parking-lots/'+lot['lot_id']+'/spaces')
    if lot['type']=='real':assert all(not s['car_id'] or (len(s['car_id'])==2 and 10<=int(s['car_id'])<=99) for s in spaces)
for path in ['/rate-policies','/sessions','/mock-payments','/alerts']:collection(path)
for value in ['7','07','00','09']:
    for path in ['/vehicles/'+value+'/location','/sticker-search?car_id='+value]:
        code,data=call(path)
        assert code==422 and data['error']['code']=='INVALID_CAR_ID' and data['error']['details']==[],(value,code)
for value in ['10','99']:
    for path in ['/vehicles/'+value+'/location','/sticker-search?car_id='+value]:
        code,data=call(path);assert code in (200,404),(value,code)
        if code==404:assert data['error']['code']=='VEHICLE_NOT_FOUND'
for path,method,expected in [('/sessions/absent/fee','GET','SESSION_NOT_FOUND'),('/parking-lots/absent/spaces','GET','PARKING_LOT_NOT_FOUND'),('/mock-payments/absent/cancel','POST','PAYMENT_NOT_FOUND'),('/alerts/absent/resolve','POST','ALERT_NOT_FOUND')]:
    code,data=call(path,method)
    assert code==404 and data['error']['code']==expected and isinstance(data['error']['details'],list),(path,code,data)
code,data=call('/mock-payments','POST',{'session_id':'absent','idempotency_key':'contract-check','simulate_failure':True})
assert code==422 and data['error']['code']=='VALIDATION_ERROR'
assert all(a['space_id'] is None for a in collection('/alerts') if a['type']=='mock_fire_led_observed')
print('PASS: sticker 10/99 accepted, 7/07/00/09 rejected; physical fixtures valid; list/error envelopes and unknown alert locations correct')
