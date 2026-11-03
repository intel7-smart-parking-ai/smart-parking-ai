"""Verify authenticated revenue totals and daily breakdowns in the deployed service."""
import json
from pathlib import Path
import runpy
from urllib.parse import urlencode
state=runpy.run_path(str(Path(__file__).with_name('verify_private_access.py')))
call=state['call'];base=state['base'];account=state['account']
assert call('/auth/login','POST',urlencode(account).encode(),origin=base)[0]==303
for query in ['month=2026-11','month=2026-11&lot_id=A','month=2026-10','month=2026-12','month=2028-02','date=2026-11-02']:
    code,body,_=call('/admin-api/demo/v1/reports/revenue?'+query)
    assert code==200,(query,code)
    report=json.loads(body)
    for key in ['net_revenue_krw','gross_amount_krw','canceled_amount_krw','completed_count','canceled_count']:
        assert sum(row[{'net_revenue_krw':'net_krw','gross_amount_krw':'gross_krw','canceled_amount_krw':'canceled_krw'}.get(key,key)] for row in report['daily_revenue'])==report['summary'][key],(query,key)
    assert report['summary']['gross_amount_krw']-report['summary']['canceled_amount_krw']==report['summary']['net_revenue_krw']
assert call('/auth/logout','POST',b'',origin=base)[0]==303
print('PASS: monthly/daily revenue, lot filter, empty periods and daily totals reconcile.')
