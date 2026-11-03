"""Create/rotate a private site account. Never print credentials to logs."""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--rotate', action='store_true')
parser.add_argument('--password-stdin', action='store_true', help='Read the chosen password from standard input')
args = parser.parse_args()
private = root / '.local-access'
private.mkdir(exist_ok=True)
if (private / 'htpasswd').exists() and not args.rotate:
    print('Private access already configured. Use --rotate to replace the password.')
    raise SystemExit(0)
password = sys.stdin.read().rstrip('\r\n') if args.password_stdin else secrets.token_urlsafe(24)
if not password or any(c in password for c in '\r\n\x00'):
    raise ValueError('Password must be nonempty and contain no line breaks or null bytes')
# Generate a SHA-512 crypt hash inside the existing Linux API container.
result = subprocess.run(
    ['docker', 'compose', 'exec', '-T', 'api', 'python', '-W', 'ignore::DeprecationWarning', '-c',
     'import crypt,sys; print(crypt.crypt(sys.stdin.read(), crypt.mksalt(crypt.METHOD_SHA512, rounds=100000)))'],
    input=password, text=True, capture_output=True, cwd=root, check=True,
)
hashed = result.stdout.strip()
if not hashed.startswith('$6$rounds=100000$'):
    raise RuntimeError('Could not generate a supported password hash')
# Update in place: Docker binds the file, so rotation must keep its inode.
(private / 'htpasswd').write_text('admin:' + hashed + '\n', encoding='ascii')
(private / 'credentials.json').write_text(json.dumps({'username':'admin','password':password},indent=2),encoding='utf-8')
(private / '로그인정보.txt').write_text('PARK:ON 비공개 접속 계정\n아이디: admin\n비밀번호: '+password+'\n\n이 파일과 비밀번호는 접속을 허용할 사람에게만 전달하세요.\n변경: python scripts/setup_private_access.py --rotate\n',encoding='utf-8')
print('Private access configured. Credentials: .local-access/로그인정보.txt')
