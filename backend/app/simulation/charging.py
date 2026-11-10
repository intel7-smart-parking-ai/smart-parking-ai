from datetime import datetime, timedelta
from hashlib import sha256
from math import ceil

CONNECT_SECONDS = 15
CHARGE_SECONDS = 120
RATED_POWER_KW = 7


def charging_status(row, now, last_used_at=None):
    status = row["status"]
    result = {"status": "unknown", "is_mock": True, "observed_at": now.isoformat(),
              "rated_power_kw": RATED_POWER_KW}
    if status == "empty":
        return {**result, "status": "available", "last_used_at": last_used_at}
    if status != "occupied":
        return result
    if row.get("vehicle_type") not in (None, "ev"):
        return {**result, "status": "not_connected"}
    if not row.get("session_id") or not row.get("entry_at"):
        return result
    entry = datetime.fromisoformat(row["entry_at"]) if isinstance(row["entry_at"], str) else row["entry_at"]
    start = entry + timedelta(seconds=CONNECT_SECONDS)
    end = start + timedelta(seconds=CHARGE_SECONDS)
    initial_percent = 20 + sha256(row["session_id"].encode("utf-8")).digest()[0] % 61
    if now < start:
        return {**result, "status": "connected", "battery_percent": initial_percent, "power_kw": 0}
    if now < end:
        elapsed = (now - start).total_seconds()
        percent = min(99, int(initial_percent + (100 - initial_percent) * elapsed / CHARGE_SECONDS))
        return {**result, "status": "charging", "started_at": start.isoformat(), "power_kw": RATED_POWER_KW,
                "battery_percent": percent, "remaining_seconds": ceil((end - now).total_seconds())}
    return {**result, "status": "completed", "completed_at": end.isoformat(), "battery_percent": 100, "power_kw": 0}


def last_charge_end(entry_at, exit_at):
    start = datetime.fromisoformat(entry_at) + timedelta(seconds=CONNECT_SECONDS)
    exit_time = datetime.fromisoformat(exit_at)
    return min(exit_time, start + timedelta(seconds=CHARGE_SECONDS)).isoformat() if exit_time > start else None
