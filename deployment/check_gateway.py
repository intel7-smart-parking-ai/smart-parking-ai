"""Isolated nginx/auth smoke test. Uses disposable containers and no parking DB.
Usage: python deployment/check_gateway.py [web-image]
"""
import http.cookiejar
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.DEVNULL).strip()


def request(url, *, method='GET', data=None, headers=None, opener=None):
    body=json.dumps(data).encode() if isinstance(data,dict) else data
    req=urllib.request.Request(url,data=body,method=method,headers=headers or {})
    try:
        with (opener or urllib.request.build_opener()).open(req,timeout=5) as res:
            return res.status,res.read()
    except urllib.error.HTTPError as exc:return exc.code,exc.read()


def main():
    image=sys.argv[1] if len(sys.argv)>1 else 'parking-docker-web-check'
    prefix='parking-gateway-test-'+uuid4().hex[:10]
    names=[prefix+'-api',prefix+'-auth',prefix+'-web']
    token='disposable-gateway-token'
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        folder=Path(directory)
        fake=folder/'api.py'
        fake.write_text("""from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok"}')
 def do_POST(self):
  accepted=self.headers.get('Authorization')=='Bearer disposable-gateway-token'
  self.send_response(202 if accepted else 403);self.end_headers();self.wfile.write(b'{}')
HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
""",encoding='utf-8')
        encoded=docker('run','--rm','python:3.11-slim','python','-W','ignore::DeprecationWarning','-c',"import crypt;print(crypt.crypt('disposable-test-password',crypt.mksalt(crypt.METHOD_SHA512)))")
        credentials=folder/'htpasswd';credentials.write_text('test-admin:'+encoded+'\n',encoding='utf-8')
        docker('network','create',prefix)
        try:
            docker('run','-d','--name',names[0],'--network',prefix,'--network-alias','api','-v',str(fake)+':/api.py:ro','python:3.12-slim','python','/api.py')
            docker('run','-d','--name',names[1],'--network',prefix,'--network-alias','admin-auth','-v',str(Path(__file__).resolve().with_name('admin_auth.py'))+':/service/admin_auth.py:ro','-v',str(credentials)+':/secrets/htpasswd:ro','python:3.11-slim','python','-W','ignore::DeprecationWarning','/service/admin_auth.py')
            docker('run','-d','--name',names[2],'--network',prefix,'-p','127.0.0.1::80','-p','127.0.0.1::81',image)
            port=lambda number:docker('port',names[2],str(number)).rsplit(':',1)[1]
            root='http://127.0.0.1:'+port(80)
            gateway='http://127.0.0.1:'+port(81)
            deadline=time.monotonic()+20
            while True:
                try:
                    if request(root+'/healthz')[0]==200:break
                except (OSError,urllib.error.URLError):pass
                if time.monotonic()>deadline:raise AssertionError('gateway startup timed out')
                time.sleep(.1)
            assert request(root+'/')[0]==200
            assert request(gateway+'/')[0]==404
            assert request(gateway+'/admin-api/team/v1/sessions')[0]==401
            assert request(root+'/api/v1/sessions')[0]==403
            assert request(root+'/api/v1/internal/vision-events',method='POST',data={})[0]==403
            assert request(root+'/api/v1/internal/vision-events',method='POST',data={},headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})[0]==202
            assert request(gateway+'/auth/login',method='POST',data=b'username=test-admin&password=disposable-test-password')[0]==403
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            request(root+'/auth/login',method='POST',data=b'username=test-admin&password=disposable-test-password',headers={'Origin':root},opener=opener)
            assert request(root+'/admin-api/team/v1/sessions',opener=opener)[0]==200
            assert request(root+'/admin-api/team/v1/simulation/C/entry',method='POST',data={},headers={'Origin':'http://untrusted.invalid'},opener=opener)[0]==403
            print('PASS web80/admin8090 separation, authenticated admin routes, CSRF, camera Bearer forwarding')
        finally:
            for name in reversed(names):
                try:docker('rm','-f',name)
                except subprocess.CalledProcessError:pass
            try:docker('network','rm',prefix)
            except subprocess.CalledProcessError:pass

if __name__=='__main__':main()
