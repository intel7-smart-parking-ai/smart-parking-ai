"""Shared vacancy wording for core answers and graph workers."""
SHORT_NAMES = {"A": "개발원", "B": "부산역", "C": "김해공항", "D": "신세계"}
SCOPES = {"ev": "전기차 전용칸", "accessible": "장애인 전용칸", "general": "일반 주차칸"}


def vacancy(rows, zone=None):
    heading = SCOPES.get(zone, "주차장") + " 빈자리는 다음과 같아요."
    return heading + "\n" + " · ".join(rows)


def lot_name(lot, fallback):
    return SHORT_NAMES.get(lot, fallback)


def facts(values):
    rows = []
    for key, value in values.items():
        first, sep, rest = key.partition(" ")
        label = (SHORT_NAMES[first] + sep + rest) if first in SHORT_NAMES else key
        rendered = f"{value:,}" if type(value) is int else str(value)
        rows.append(f"{label}: {rendered}" if label else rendered)
    return "\n".join(rows)
