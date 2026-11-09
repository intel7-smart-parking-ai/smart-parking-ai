from datetime import datetime, timedelta

CONNECT_SECONDS = 15
CHARGE_SECONDS = 120


def charging_status(row, now, last_used_at=None):
    status = row["status"]
    result = {"status": "unknown", "is_mock": True, "observed_at": now.isoformat()}
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
    if now < start:
        return {**result, "status": "connected"}
    if now < end:
        return {**result, "status": "charging", "started_at": start.isoformat(), "power_kw": 7}
    return {**result, "status": "completed", "completed_at": end.isoformat()}


def last_charge_end(entry_at, exit_at):
    start = datetime.fromisoformat(entry_at) + timedelta(seconds=CONNECT_SECONDS)
    exit_time = datetime.fromisoformat(exit_at)
    return min(exit_time, start + timedelta(seconds=CHARGE_SECONDS)).isoformat() if exit_time > start else None
