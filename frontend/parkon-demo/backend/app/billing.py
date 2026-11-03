"""Server-only integration boundary. Fixture quotes are NOT the official billing module.

Replace this provider with Yoon Myeong-su's server module when delivered.
No time-based fee formula is copied into React or this temporary provider.
"""
import os

POLICIES = {
    'A': dict(base_minutes=30, base_fee=1000, unit_minutes=10, extra_fee=500, daily_cap=10000),
    'B': dict(base_minutes=30, base_fee=1200, unit_minutes=10, extra_fee=600, daily_cap=12000),
    'C': dict(base_minutes=60, base_fee=1000, unit_minutes=15, extra_fee=400, daily_cap=8000),
    'D': dict(base_minutes=30, base_fee=800, unit_minutes=10, extra_fee=300, daily_cap=7000),
}
QUOTES = {
    'A': dict(amount_krw=5000, base_amount_krw=1000, extra_units=8, extra_amount_krw=4000),
    'B': dict(amount_krw=6000, base_amount_krw=1200, extra_units=8, extra_amount_krw=4800),
    'C': dict(amount_krw=2600, base_amount_krw=1000, extra_units=4, extra_amount_krw=1600),
    'D': dict(amount_krw=3200, base_amount_krw=800, extra_units=8, extra_amount_krw=2400),
}
FIXED_TIME = '2026-11-02T14:30:00+09:00'

def quote(session, policy):
    if os.getenv('BILLING_PROVIDER', 'fixtures') != 'fixtures':
        raise RuntimeError('Official billing provider has not been installed.')
    return {
        **QUOTES[session['lot_id']], 'policy': policy,
        'session_id': session['session_id'], 'quote_id': 'quote-' + session['session_id'],
        'duration_seconds': 6600, 'duration_minutes': 110,
        'cap_discount_krw': 0, 'calculated_at': FIXED_TIME,
        'is_mock': True, 'authority': 'server_fixture',
    }
