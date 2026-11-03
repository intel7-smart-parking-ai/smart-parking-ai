import json
import os
from pathlib import Path
from urllib.request import urlopen
record=json.loads(Path(__file__).with_name('verification-record.json').read_text())
def get(path):
    with urlopen(os.environ.get('PARKON_TEST_URL','http://127.0.0.1:8080')+'/demo-api/v1'+path,timeout=15) as response:return json.load(response)
payments=get('/mock-payments');alerts=get('/alerts')
assert any(p['payment_id']==record['payment_id'] and p['status']=='cancelled' for p in payments)
assert any(a['alert_id']==record['alert_id'] and a['status']=='resolved' for a in alerts)
print('PASS: payment and alert records survived database/API container restart.')
