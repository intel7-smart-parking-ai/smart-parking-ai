import hashlib
from deployment.admin_auth import verify_password


def test_generated_password_format_accepts_only_original_password():
    salt = '1234abcd'
    digest = hashlib.pbkdf2_hmac('sha256', b'test-password', bytes.fromhex(salt), 600000).hex()
    encoded = f'pbkdf2_sha256$600000${salt}${digest}'
    assert verify_password('test-password', encoded)
    assert not verify_password('wrong-password', encoded)
