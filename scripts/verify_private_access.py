"""Verify public/admin separation, sessions and CSRF without logging credentials."""
import http.cookiejar
import json
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlencode

base=(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8080').rstrip('/')
account=json.loads((Path(__file__).resolve().parent.parent/'.local-access/credentials.json').read_text(encoding='utf-8'))
jar=http.cookiejar.CookieJar()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar),NoRedirect())
def call(path,method='GET',body=None,origin=None,headers=None,retry=0):
    headers=dict(headers or {})
    if origin: headers['Origin']=origin
    if body is not None: headers['Content-Type']='application/x-www-form-urlencoded'
    req=urllib.request.Request(base+path,data=body,method=method,headers=headers)
    try:
        with opener.open(req,timeout=30) as r:return r.status,r.read(),r.headers
    except urllib.error.HTTPError as e:
        if e.code==429 and retry<6:
            e.close()
            time.sleep(1.1)
            return call(path,method,body,origin,headers,retry+1)
        return e.code,e.read(),e.headers
for path in ['/','/?app=1','/demo-api/v1/parking-lots','/demo-api/v1/rate-policies','/demo-api/v1/alerts','/api/v1/parking-lots','/admin/login']:
    assert call(path)[0]==200,path
for path in ['/admin-api/demo/v1/reports/revenue?month=2026-11','/admin-api/demo/v1/mock-payments','/admin-api/demo/v1/sessions','/admin-api/team/v1/parking-lots']:
    assert call(path)[0]==401,path
for path in ['/demo-api/v1/reports/revenue','/demo-api/v1/sessions','/demo-api/v1/mock-payments']:
    assert call(path)[0]==403,path
for path in ['/demo-api/v1/mock-payments/no-such-payment/cancel','/demo-api/v1/demo/alerts','/demo-api/v1/alerts/no-such-alert/resolve']:
    assert call(path,'POST',b'{}')[0]==403,path
assert b'name="password"' in call('/admin/')[1]
assert call('/auth/login','POST',urlencode(account).encode(),origin='https://wrong.example')[0]==403
assert call('/auth/login','POST',b'username=admin&password=incorrect',origin=base)[0]==401
code,_,headers=call('/auth/login','POST',urlencode(account).encode(),origin=base)
assert code==303 and headers['Location']=='/admin/',code
cookie=headers['Set-Cookie'];assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
for path in ['/admin/','/admin-api/demo/v1/reports/revenue?month=2026-11','/admin-api/demo/v1/mock-payments','/admin-api/demo/v1/sessions']:
    assert call(path)[0]==200,path
# A cross-origin authenticated mutation must be rejected before reaching the backend.
assert call('/admin-api/demo/v1/alerts/no-such-alert/resolve','POST',b'{}',origin='https://wrong.example')[0]==403
assert call('/auth/logout','POST',b'',origin='https://wrong.example')[0]==403
assert call('/auth/logout','POST',b'',origin=base)[0]==303
assert call('/admin-api/demo/v1/sessions')[0]==401
assert call('/admin-api/demo/v1/sessions',headers={'Cookie':'parkon_admin_session=forged'})[0]==401
assert call('/')[0]==200
print('PASS: public service, protected administrator APIs, login, invalid password, CSRF, logout, forged-cookie rejection.')
