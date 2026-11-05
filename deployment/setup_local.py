"""Create private Docker credentials on first run; never overwrite existing files."""
from getpass import getpass
import hashlib
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    credentials = root / '.local-access' / 'htpasswd'
    if not credentials.exists():
        username = input('관리자 아이디: ').strip()
        if not username or ':' in username or '\n' in username:
            raise SystemExit('유효한 관리자 아이디를 입력해주세요.')
        password = getpass('관리자 비밀번호 (8자 이상): ')
        if len(password) < 8 or password != getpass('비밀번호 확인: '):
            raise SystemExit('비밀번호 길이 또는 확인 입력을 확인해주세요.')
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
        credentials.parent.mkdir(exist_ok=True)
        with credentials.open('x', encoding='utf-8') as file:
            file.write(f'{username}:pbkdf2_sha256$600000${salt}${digest}\n')
    env = root / '.env'
    if not env.exists():
        text = (root / '.env.example').read_text(encoding='utf-8')
        text = text.replace('VISION_TOKEN=\n', 'VISION_TOKEN=' + secrets.token_urlsafe(32) + '\n')
        text = text.replace('ALERTS_INTERNAL_TOKEN=\n', 'ALERTS_INTERNAL_TOKEN=' + secrets.token_urlsafe(32) + '\n')
        with env.open('x', encoding='utf-8') as file:
            file.write(text)
    print('로컬 설정 준비 완료. 기존 파일은 보존했습니다. docker compose up --build -d 를 실행하세요.')


if __name__ == '__main__':
    main()
