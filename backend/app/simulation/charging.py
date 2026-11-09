"""Virtual charger telemetry, separate from camera occupancy evidence."""
from datetime import datetime, timedelta
from math import ceil


def telemetry(row, now, enabled):
    if row.get('space_type') != 'ev':
        return None
    base = {'source': 'simulation' if enabled else 'not_connected',
            'is_mock': enabled, 'power_kw': 0.0 if enabled else None,
            'observed_at': now.isoformat(), 'connected': False,
            'estimated_remaining_seconds': None, 'estimated_completion_at': None}
    if not enabled:
        return {**base, 'status': 'unknown'}
    if row['status'] in ('uncertain', 'unavailable'):
        return {**base, 'status': 'unknown', 'power_kw': None}
    if row['status'] != 'occupied':
        return {**base, 'status': 'available'}
    # In the virtual simulator EV bays receive EVs unless an explicit type says otherwise.
    if row.get('vehicle_type') not in (None, 'ev'):
        return {**base, 'status': 'not_connected'}
    if not row.get('session_id') or not row.get('entry_at'):
        return {**base, 'status': 'unknown', 'power_kw': None}
    entry = row['entry_at']
    entry = datetime.fromisoformat(entry.replace('Z', '+00:00')) if isinstance(entry, str) else entry
    elapsed = max(0, (now-entry).total_seconds())
    # Deliberately accelerated demo, never a physical charging measurement.
    status = 'connected' if elapsed < 15 else 'charging' if elapsed < 135 else 'completed'
    return {**base, 'connected': True, 'status': status,
            'estimated_remaining_seconds': max(0,ceil(135-elapsed)),
            'estimated_completion_at': (entry+timedelta(seconds=135)).isoformat(),
            'power_kw': 7.0 if status == 'charging' else 0.0,
            'demo_cycle': '입차 후 연결 대기 15초 → 충전 120초 → 완료'}
