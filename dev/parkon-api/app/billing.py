"""Server-only integration boundary. Fixture quotes are NOT the official billing module.

Replace this provider with Yoon Myeong-su's server module when delivered.
No time-based fee formula is copied into React or this temporary provider.
"""
import os
from datetime import datetime,timezone

POLICIES = {
    'A': dict(base_minutes=30, base_fee=1000, unit_minutes=10, extra_fee=500, daily_cap=10000),
    'B': dict(base_minutes=30, base_fee=2000, unit_minutes=10, extra_fee=700, daily_cap=20000),
    'C': dict(base_minutes=30, base_fee=3000, unit_minutes=10, extra_fee=800, daily_cap=40000),
    'D': dict(base_minutes=30, base_fee=4000, unit_minutes=10, extra_fee=1000, daily_cap=50000),
}
QUOTES = {
    'A': dict(amount_krw=5000, base_amount_krw=1000, extra_units=8, extra_amount_krw=4000),
    'B': dict(amount_krw=7600, base_amount_krw=2000, extra_units=8, extra_amount_krw=5600),
    'C': dict(amount_krw=9400, base_amount_krw=3000, extra_units=8, extra_amount_krw=6400),
    'D': dict(amount_krw=12000, base_amount_krw=4000, extra_units=8, extra_amount_krw=8000),
}
FIXED_TIME = '2026-11-02T14:30:00+09:00'

def quote(session, policy):
    if os.getenv('BILLING_PROVIDER', 'fixtures') != 'fixtures':
        raise RuntimeError('Official billing provider has not been installed.')
    calculated_at=FIXED_TIME
    seconds=6600
    if session.get('is_simulated'):
        now=session.get('exit_at') or datetime.now(timezone.utc)
        calculated_at=now.isoformat()
        seconds=max(0,int((now-session['entry_at']).total_seconds()))
    return {
        **QUOTES[session['lot_id']], 'policy': policy,
        'session_id': session['session_id'], 'quote_id': 'quote-' + session['session_id'],
        'duration_seconds': seconds, 'duration_minutes': seconds//60,
        'cap_discount_krw': 0, 'calculated_at': calculated_at,
        'entry_at': session['entry_at'], 'rate_policy': policy,
        'fee_breakdown': dict(base_fee_krw=QUOTES[session['lot_id']]['base_amount_krw'],extra_units=QUOTES[session['lot_id']]['extra_units'],extra_fee_krw=QUOTES[session['lot_id']]['extra_amount_krw'],daily_cap_krw=policy['daily_cap'],cap_discount_krw=0),
        'is_mock': True, 'authority': 'server_fixture',
    }
