"""상황(사고) 보고서 자동 작성.

경보 단계가 바뀔 때마다 기록을 쌓아서 보고서 초안을 실시간으로 만든다.
- 진행 중: 관리자가 영향 차량·안내 현황을 보며 대응 체크리스트로 사용
- 해제 후: 관리자가 조치 내용을 덧붙여 검토하고 확정
안전 보고서 자동완성과 같은 발상: 사람이 처음부터 쓰지 않고, 시스템이 쓴 초안을 검토만 한다.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from functools import wraps

from .retention import prune_finished, warn_if_protected
from . import config
from .adapters import DataUnavailable, iso, now
from . import briefing
from .templates import alert_location

CHECKLIST = [
    "현장 확인 (카메라·육안)",
    "근접 차량 차주 안내 (현장 접근 금지)",
    "대피 안내 방송",
    "119 신고 여부 판단 (시연: 모의 상황)",
    "해제 및 운영 재개 확인",
]

_lock = threading.RLock()
INCIDENTS: list[dict] = []
_state = {"last_version": 0, "current": None, "next_id": 1, "pruned": 0}


def _locked(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapped


def _hhmm(iso_str: str) -> str:
    return iso_str[11:16]


def _add(inc: dict, text: str) -> None:
    inc["timeline"].append({"at": iso(now()), "text": text})


def _near(lot, spot, other):
    """같은 줄에서 번호가 하나 차이 나는 칸(도면 번호 기준). 칸을 모르면 주차장 전체."""
    return spot is None or config.catalog.is_near(lot, spot, other)


def _report_location(alert):
    if alert.get("spot_no") is None:
        return alert_location(alert)
    return f"{config.catalog.place(alert['lot_id'], alert['spot_no'])} 부근"


def _cars(data, lot_id):
    try:
        return data.list_cars(lot_id), True
    except DataUnavailable:
        return [], False


def _trim():
    _state["pruned"] += prune_finished(
        INCIDENTS, config.INCIDENT_LIMIT, lambda inc: inc["status"] == "확정"
    )
    warn_if_protected("incidents", len(INCIDENTS), config.INCIDENT_LIMIT)


def pruned_count():
    with _lock:
        return _state["pruned"]


def observe(alert: dict, data) -> int | None:
    """Fetch vehicles without holding the report lock, then apply the alert version."""
    version = alert.get("version", 0)
    level = alert.get("level", 0)
    while True:
        with _lock:
            observed = _state["last_version"]
            cur = _state["current"]
            if alert.get("stale") or version <= observed:
                return (cur or {}).get("id")
            new_location = cur is None or (cur["lot_id"], cur["spot_no"]) != (alert.get("lot_id"), alert.get("spot_no"))
            need_affected = level > 0 and (new_location or not cur["affected_known"])
        first = _cars(data, alert.get("lot_id")) if need_affected else None
        need_briefing = level >= 2 and (first is None or (not new_location and not first[1]))
        latest = _cars(data, alert.get("lot_id")) if need_briefing else None
        with _lock:
            if observed == _state["last_version"]:
                return _apply_observation(alert, version, first, latest)


def _apply_observation(alert, version, first_read, briefing_read):
    """Commit an observation and its already-fetched vehicle evidence."""
    _state["last_version"] = version
    level = alert.get("level", 0)
    cur = _state["current"]

    if level > 0:
        fresh_cars = None
        if cur and (cur["lot_id"], cur["spot_no"]) != (alert["lot_id"], alert["spot_no"]):
            cur["status"] = "위치 변경 · 검토 대기"
            cur["revision"] += 1
            cur["closed_at"] = iso(now())
            _add(cur, "경보 위치 변경으로 기록 종료 (실제 해제 여부는 관리자 확인 필요)")
            cur = None
        label = "이상 신호 확인 중 (Lv1)" if level == 1 else "경보 발령 (Lv2)"
        if cur is None:
            lot, spot = alert["lot_id"], alert["spot_no"]
            cars, affected_known = first_read
            fresh_cars = (cars, affected_known)
            cur = {
                "id": _state["next_id"],
                "status": "진행 중",
                "revision": 1,
                "event_type": alert.get("event_type", "mock_fire_led_observed"),
                "lot_id": lot, "spot_no": spot, "space_id": config.catalog.space_label(lot, spot),
                "opened_at": iso(now()), "closed_at": None, "confirmed_at": None,
                "max_level": level, "max_confidence": alert.get("confidence", 0),
                "affected": cars,
                "affected_known": affected_known,
                "affected_at": iso(now()) if affected_known else None,
                "affected_recovered": False,
                "near": [c["car_id"] for c in cars if _near(lot, spot, c["spot_no"])],
                "near_spots": [c["spot_no"] for c in cars if _near(lot, spot, c["spot_no"])],
                "notified": set(), "evacuation_requests": 0, "near_queries": 0,
                "admin_calls": [],
                "checklist": [{"item": x, "done_at": None} for x in CHECKLIST],
                "notes": "", "timeline": [],
            }
            _state["next_id"] += 1
            INCIDENTS.append(cur)
            _trim()
            _state["current"] = cur
            _add(cur, f"{_report_location(alert)} {label}, 신뢰도 {round(cur['max_confidence'] * 100)}%")
        else:
            cur["revision"] += 1
            cur["max_level"] = max(cur["max_level"], level)
            cur["max_confidence"] = max(cur["max_confidence"], alert.get("confidence", 0))
            _add(cur, f"{label}, 신뢰도 {round(alert.get('confidence', 0) * 100)}%")
            if not cur["affected_known"]:
                cars, known = first_read
                if known:
                    fresh_cars = (cars, True)
                    cur.update(affected=cars, affected_known=True, affected_recovered=True,
                               affected_at=iso(now()), near=[c["car_id"] for c in cars
                               if _near(cur["lot_id"], cur["spot_no"], c["spot_no"])],
                               near_spots=[c["spot_no"] for c in cars if _near(cur["lot_id"], cur["spot_no"], c["spot_no"])])
                    _add(cur, "영향 차량 재조회 성공 (발생 당시 목록은 미확인)")
        if level >= 2:
            if fresh_cars is not None:
                latest_cars, latest_known = fresh_cars
            else:
                latest_cars, latest_known = briefing_read
            cur["briefing"] = briefing.build(alert, latest_cars, latest_known)
    elif cur is not None:
        cur["status"] = "해제 · 검토 대기"
        cur["revision"] += 1
        cur["closed_at"] = iso(now())
        _add(cur, "경보 해제")
        _state["current"] = None
    return (_state["current"] or {}).get("id")


@_locked
def active() -> dict | None:
    return deepcopy(_state["current"])


@_locked
def mark_notified(session_id: str, inc_id=None) -> None:
    inc = get(inc_id) if inc_id is not None else _state["current"]
    if inc and inc["status"] != "확정":
        inc["notified"].add(session_id)


@_locked
def count(kind: str, inc_id=None) -> None:
    cur = get(inc_id) if inc_id is not None else _state["current"]
    if cur:
        cur[kind] += 1


@_locked
def link_admin_call(call_id: int, inc_id: int | None) -> None:
    cur = get(inc_id) if inc_id is not None else None
    if cur and call_id not in cur["admin_calls"]:
        cur["admin_calls"].append(call_id)
        cur["revision"] += 1
        _add(cur, f"이용자 관리자 호출 #{call_id}")


@_locked
def set_check(inc_id: int, idx: int, checked: bool, expected_revision=None) -> dict | None:
    inc = get(inc_id)
    if not inc or not (0 <= idx < len(inc["checklist"])):
        return None
    if inc["status"] == "확정":
        return {"error": "확정된 보고서는 수정할 수 없어요."}
    if type(checked) is not bool:
        return {"error": "checked는 true 또는 false여야 합니다."}
    item = inc["checklist"][idx]
    # 동일한 최종 상태의 재전송은 기록을 추가하거나 되돌리지 않는다.
    if bool(item["done_at"]) == checked:
        return public(inc)
    if expected_revision is not None and expected_revision != inc["revision"]:
        return {"error": "다른 변경이 있어요. 최신 보고서를 확인한 뒤 다시 저장해 주세요.", "conflict": True}
    if not checked:
        item["done_at"] = None
        _add(inc, f"조치 취소: {item['item']}")
    else:
        item["done_at"] = iso(now())
        _add(inc, f"조치 완료: {item['item']}")
    inc["revision"] += 1
    return public(inc)


@_locked
def set_notes(inc_id: int, text: str, expected_revision=None) -> dict | None:
    inc = get(inc_id)
    if not inc:
        return None
    if inc["status"] == "확정":
        return {"error": "확정된 보고서는 수정할 수 없어요."}
    if expected_revision is not None and expected_revision != inc["revision"]:
        return {"error": "다른 변경이 있어요. 최신 보고서를 확인한 뒤 다시 저장해 주세요.", "conflict": True}
    if not isinstance(text, str) or len(text) > 1000:
        return {"error": "메모는 1000자 이하 문자열이어야 합니다."}
    inc["notes"] = text.strip()[:1000]
    inc["revision"] += 1
    return public(inc)


@_locked
def confirm(inc_id: int, expected_revision=None, notes=None) -> dict:
    if notes is not None and (not isinstance(notes, str) or len(notes) > 1000):
        return {"error": "메모는 1000자 이하 문자열이어야 합니다."}
    inc = get(inc_id)
    if not inc:
        return {"error": "없는 보고서"}
    if inc["status"] == "확정":
        if notes is not None and notes.strip() != inc["notes"]:
            return {"error": "이미 다른 내용으로 확정된 보고서입니다.", "conflict": True}
        return public(inc)
    if expected_revision is not None and expected_revision != inc["revision"]:
        return {"error": "다른 변경이 있어요. 최신 보고서를 확인한 뒤 다시 확정해 주세요.", "conflict": True}
    if inc["closed_at"] is None:
        return {"error": "경보가 진행 중이라 확정할 수 없어요. 해제 후 확정하세요."}
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > 1000:
            return {"error": "메모는 1000자 이하 문자열이어야 합니다."}
        inc["notes"] = notes.strip()
    inc["status"] = "확정"
    inc["revision"] += 1
    inc["confirmed_at"] = iso(now())
    result = public(inc)
    _trim()
    return result


def get(inc_id: int) -> dict | None:
    return next((x for x in INCIDENTS if x["id"] == inc_id), None)


def report_text(inc: dict) -> str:
    """보고서 초안 문장. 진행 중에도 호출할 때마다 최신 내용으로 다시 만든다."""
    lv = "Lv2 경보" if inc["max_level"] >= 2 else "Lv1 확인 단계"
    end = _hhmm(inc["closed_at"]) if inc["closed_at"] else "진행 중"
    done = [c for c in inc["checklist"] if c["done_at"]]
    todo = [c["item"] for c in inc["checklist"] if not c["done_at"]]
    lines = [
        f"[모의 화재(LED) 상황 보고서 초안 #{inc['id']}] 상태: {inc['status']}",
        "",
        "1. 개요",
        f"  {inc['opened_at'][:10]} {_hhmm(inc['opened_at'])} {_report_location(inc)} 모의 화재(LED) 감지",
        f"  최고 단계 {lv}, 최대 신뢰도 {round(inc['max_confidence'] * 100)}%, 종료 {end}",
        "",
        "2. 경과",
        *[f"  {_hhmm(t['at'])} {t['text']}" for t in inc["timeline"]],
        "",
        "3. 영향 범위",
        (f"  {inc['lot_id']} 주차장 주차 차량 {len(inc['affected'])}대" if inc.get("affected_known", True)
         else "  차량 목록 조회 실패 · 영향 범위 미확인")
        + ((f" (근접 차량: {', '.join(('번호 미인식' if n is None else n + '번') for n in inc['near'])})" if inc["near"] else " (근접 차량 없음)")
           if inc.get("affected_known") else "")
        + (f" · 재조회 시각 {inc['affected_at']} (발생 당시 목록은 미확인)" if inc.get("affected_recovered") else ""),
        "",
        "4. 이용자 안내·문의",
        f"  챗봇 경보 수신 확인 {len(inc['notified'])}세션, 대피 안내 요청 {inc['evacuation_requests']}건, "
        f"내 차 근처 문의 {inc['near_queries']}건, 관리자 호출 {len(inc['admin_calls'])}건",
        "",
        "5. 조치 내용",
        *([f"  {_hhmm(c['done_at'])} {c['item']}" for c in done] or ["  (기록된 조치 없음)"]),
        *([f"  미완료: {', '.join(todo)}"] if todo else []),
        *([f"  관리자 메모: {inc['notes']}"] if inc["notes"] else []),
        "",
        *(["6. 소방 전달용 현장 정보", inc["briefing"]["text"], ""] if inc.get("briefing") else []),
        "비고",
        "  시연용 모의 상황이며 실제 화재가 아님. 시스템이 자동 작성한 초안이므로 관리자 검토 후 확정.",
    ]
    return "\n".join(lines)


@_locked
def public(inc: dict) -> dict:
    out = deepcopy({k: v for k, v in inc.items() if k != "notified"})
    out["notified_count"] = len(inc["notified"])
    out["report"] = report_text(inc)
    return out


def list_public() -> list[dict]:
    with _lock:
        return [public(x) for x in reversed(INCIDENTS)]


def reset() -> None:
    with _lock:
        INCIDENTS.clear()
        _state["last_version"] = 0
        _state["current"] = None
        _state["next_id"] = 1
        _state["pruned"] = 0
