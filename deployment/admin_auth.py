"""Private administrator sessions; reachable only on the Docker network."""
try:
    import crypt
except ImportError:
    crypt = None
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

sessions = {}
failures = {}
lock = threading.Lock()
TTL = 8 * 60 * 60
COOKIE = 'parkon_admin_session'


def verify_password(password, hashed):
    if hashed.startswith('pbkdf2_sha256$'):
        _, iterations, salt, expected = hashed.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    return crypt is not None and hmac.compare_digest(crypt.crypt(password, hashed) or '', hashed)

def page(error=''):
    return ('''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PARK:ON 관리자 로그인</title><style>*{box-sizing:border-box}body{margin:0;background:#f1f4ec;color:#263725;font:15px system-ui;display:grid;min-height:100vh;place-items:center;padding:24px}.card{width:100%;max-width:420px;background:white;padding:36px;border:1px solid #d6dfcf;border-radius:18px;box-shadow:0 15px 60px #28422312}h1{font-size:25px;margin:12px 0}p{color:#718168;line-height:1.7;font-size:14px}label{display:block;margin:22px 0 8px;font-weight:600;font-size:13px}input{width:100%;padding:13px;border:1px solid #bdcdb1;border-radius:7px;font:inherit}button{width:100%;margin:26px 0 18px;background:#37572c;color:white;padding:14px;border:0;border-radius:8px;font:inherit;font-weight:700;cursor:pointer}a{color:#526d3d;font-size:13px}.error{background:#fff1ed;color:#9a3327;padding:12px;border-radius:6px}.brand{font-weight:900;letter-spacing:1px;color:#8b7c32}</style><main class="card"><div class="brand">PARK:ON</div><h1>관리자 로그인</h1><p>주차장 운영 및 매출 관리 전용입니다.<br>관리자 계정으로 로그인해주세요.</p>''' + ('<p class="error" role="alert">'+error+'</p>' if error else '') + '''<form method="post" action="/auth/login"><label for="username">아이디</label><input id="username" name="username" autocomplete="username" required maxlength="64"><label for="password">비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256"><button type="submit">관리자 로그인</button></form><a href="/">사용자 페이지로 이동</a></main></html>''').encode()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body=b'', headers=None):
        self.send_response(code)
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Type','text/html; charset=utf-8')
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def same_origin(self):
        origin = self.headers.get('Origin','')
        return bool(origin) and urlsplit(origin).netloc == self.headers.get('Host') and urlsplit(origin).scheme in ('http','https')

    def token(self):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            return cookie[COOKIE].value if COOKIE in cookie else ''
        except Exception: return ''

    def do_GET(self):
        if self.path=='/healthz': return self.reply(200,b'ok')
        if self.path=='/admin/login': return self.reply(200,page())
        if self.path!='/check': return self.reply(404)
        token=self.token()
        try: encoded=Path('/secrets/htpasswd').read_bytes()
        except OSError: return self.reply(503)
        fingerprint=hashlib.sha256(encoded).digest()
        now=time.time()
        with lock:
            for key in list(sessions):
                if sessions[key][0] <= now or sessions[key][1]!=fingerprint: sessions.pop(key,None)
            valid=bool(token) and token in sessions
        if not valid: return self.reply(401)
        if self.headers.get('X-Original-Method','GET') not in ('GET','HEAD','OPTIONS') and not self.same_origin():
            return self.reply(403)
        return self.reply(204)

    def do_POST(self):
        if self.path not in ('/auth/login','/auth/logout'): return self.reply(404)
        if not self.same_origin(): return self.reply(403,page('올바른 사이트에서 다시 로그인해주세요.'))
        secure='; Secure' if self.headers.get('X-Forwarded-Proto')=='https' else ''
        if self.path=='/auth/logout':
            with lock: sessions.pop(self.token(),None)
            return self.reply(303,headers={'Location':'/admin/login','Set-Cookie':COOKIE+'=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'+secure})
        try: size=int(self.headers.get('Content-Length','0'))
        except ValueError: return self.reply(400)
        if size<1 or size>4096: return self.reply(413)
        fields=parse_qs(self.rfile.read(size).decode('utf-8',errors='replace'))
        username=fields.get('username',[''])[0]; password=fields.get('password',[''])[0]
        key=self.headers.get('X-Real-IP','unknown');now=time.time()
        with lock:
            for address in list(failures):
                failures[address]=[t for t in failures[address] if t>now-300]
                if not failures[address]: failures.pop(address,None)
            if len(failures.get(key,[]))>=5: return self.reply(429,page('로그인 시도가 많습니다. 5분 후 다시 시도해주세요.'))
        try:
            encoded=Path('/secrets/htpasswd').read_bytes()
            account,hashed=encoded.decode().strip().split(':',1)
            valid=hmac.compare_digest(username.encode(),account.encode()) and verify_password(password,hashed)
        except (OSError,ValueError): return self.reply(503)
        if not valid:
            with lock: failures.setdefault(key,[]).append(now)
            return self.reply(401,page('아이디 또는 비밀번호를 확인해주세요.'))
        token=secrets.token_urlsafe(32)
        with lock:
            failures.pop(key,None)
            for old in list(sessions):
                if sessions[old][0]<=now: sessions.pop(old,None)
            if len(sessions)>=1000: sessions.pop(next(iter(sessions)))
            sessions[token]=(now+TTL,hashlib.sha256(encoded).digest())
        return self.reply(303,headers={'Location':'/admin/','Set-Cookie':COOKIE+'='+token+'; Path=/; Max-Age='+str(TTL)+'; HttpOnly; SameSite=Strict'+secure})

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0',8090),Handler).serve_forever()
