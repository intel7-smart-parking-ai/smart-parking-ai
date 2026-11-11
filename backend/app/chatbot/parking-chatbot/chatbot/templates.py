"""답변 문장 템플릿. 말투를 바꾸려면 이 파일만 고치면 된다.

원칙: 결론·숫자 먼저, 두 줄 이내, 쉬운 말("미리 결제", "들어온 시간"),
경보는 실제 화재가 아니라 '모의 화재(LED) 감지'로 표기.
"""
from datetime import datetime
from . import config, contracts as C


def hhmm(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).strftime("%H:%M") if iso_str else "미확인"


def won(n: int) -> str:
    return f"{n:,}원"


WELCOME = "주차장 안내봇이에요. 빈자리, 내 차 위치, 요금·미리 결제를 도와드려요.\n아래 버튼을 누르거나 편하게 물어보세요."
HELP = "이런 걸 할 수 있어요: 빈자리 확인, 내 차 위치, 요금 확인과 미리 결제, 관리자 호출.\n아래 버튼을 눌러 보세요."
THANKS = "천만에요. 더 필요한 게 있으면 말씀해 주세요."

# 실물 A·B는 스티커 두 자리, 가상 C·D는 번호판 뒤 4자리(봇 점검 11/11: 4자리 차에 '두 자리'를 되물음)
ASK_VEHICLE = "차량 번호를 알려주세요. 차 지붕 스티커의 두 자리 숫자예요. (예: 27)\n김해공항·신세계 주차장은 번호판 뒤 4자리를 알려주세요."
ASK_DIFFERENT_VEHICLE = f"다른 차량 번호를 알려주세요. 스티커의 두 자리 숫자({config.VEHICLE_MIN}~{config.VEHICLE_MAX})예요."
INVALID_VEHICLE = f"차량 번호는 {config.VEHICLE_MIN}부터 {config.VEHICLE_MAX}까지 두 자리 숫자예요."
SHORT_VEHICLE = ("한 자리이거나 0으로 시작하는 번호는 쓰지 않아서 조회할 차량이 없어요. "
                 f"차량 번호는 두 자리로 말씀해 주세요 ({config.VEHICLE_MIN}~{config.VEHICLE_MAX}, 예: 27).")
UNASSIGNED_VEHICLE = "등록되지 않은 차량 번호예요. 스티커 번호를 다시 확인해 주세요."
VIRTUAL_VEHICLE_PENDING = "가상 주차장 차량 검색은 준비 중이에요. 실물 차량은 두 자리 스티커 번호로 알려주세요."
EXIT_HELP = "출구 문제는 현장 관리자가 도와드려요. 아래 버튼으로 관리자를 부를 수 있어요."
CONFIRM_VEHICLE = "{no}번 차 맞으세요?"
VEHICLE_SET = "{no}번 차로 기억해 둘게요."
VEHICLE_SWITCH = "{no}번 차로 바꿔서 볼게요."
VEHICLE_DENIED = "알겠어요. 차량 번호를 다시 알려주세요."
NOTHING_TO_CONFIRM = "확인할 내용이 없어요. 무엇을 도와드릴까요?"
CANCEL_ADMIN = "알겠어요. 새 관리자 호출은 하지 않을게요."
SUGGEST_ADMIN_CALL = "관리자를 부를까요?\n아래 버튼을 누르면 바로 접수돼요."
CANCEL_PAYMENT = "새 결제는 진행하지 않을게요."
CANCEL_PENDING = "진행 중이던 요청을 취소했어요."
CANCEL_NONE = "진행 중인 요청은 없어요."
REFUND_UNSUPPORTED = "이미 완료한 결제의 환불은 이 기능에서 처리하지 않습니다."

UNKNOWN_FIRST = "잘 이해하지 못했어요. 저는 주차 안내(빈자리, 내 차 위치, 요금·모의 결제)만 도와드려요. 아래 버튼에서 골라 주세요."
UNKNOWN_AGAIN = "아래에서 골라 주시거나, 관리자를 불러 드릴게요."


def place(lot_id, spot=None, label=None) -> str:
    """'개발원주차장 B-06' (주차장 이름 + 도면 번호). 칸을 모르면 주차장 이름만."""
    label = label or config.catalog.space_label(lot_id, spot)
    return f"{config.catalog.name(lot_id)} {label}" if label else config.catalog.name(lot_id)


VEHICLE_TYPE_LABELS = {"ev": "전기차", "accessible": "장애인 차량"}


def car_location(d: dict) -> str:
    kind = VEHICLE_TYPE_LABELS.get(d.get("vehicle_type"))
    who = f"{d['car_id']}번 차" + (f"({kind})" if kind else "")
    where = place(d["lot_id"], d.get("spot_no"), d.get("space_id"))
    if d.get("status") == "uncertain":
        return f"{who}는 {where}로 보이지만 확인 중이에요."
    return f"{who}는 {where}에 있어요."


CAR_NOT_FOUND = "{no}번 차의 입차 기록이 없어요. 번호가 맞는지 확인해 주세요."
CAR_PENDING = "{no}번 차는 {where}입차 확인 중이에요. 자리가 확인되면 위치를 알려 드릴 수 있어요. 잠시 후 다시 물어봐 주세요."


def car_pending(d: dict) -> str:
    lot = d.get("lot_id")
    return CAR_PENDING.format(no=d["car_id"], where=f"{config.catalog.name(lot)} " if lot else "")


def fee(d: dict) -> str:
    if d.get("api_result"):
        closed = "출차 완료된 주차예요. " if d.get("session_status") == C.SESSION_CLOSED else ""
        if d.get("paid"):
            receipt = d.get("payment") if isinstance(d.get("payment"), dict) else {}
            amount = receipt.get("amount_krw") if type(receipt.get("amount_krw")) is int else d.get("fee")
            return closed + f"{d['car_id']}번 차는 이미 결제했어요" + (f"(결제 금액 {won(amount)})." if type(amount) is int else ".")
        text = closed + ("결제가 취소된 상태예요. " if d.get("payment_status") == C.PAYMENT_CANCELED else "") + f"현재 요금은 {won(d['fee'])}이에요."
        if d.get("is_daily_capped"):
            text += " 하루 최대 요금이 적용됐어요."
        return text
    if d["paid"]:
        return f"{d['car_id']}번 차는 이미 결제했어요."
    if d["fee"] == 0:
        left = d["free_minutes"] - d["minutes"]
        return f"아직 무료 시간이에요. {left}분 안에 나가면 0원이에요."
    text = f"지금 나가면 {won(d['fee'])}이에요. 30분 뒤에는 {won(d['fee_after_30'])}이 돼요."
    if d.get("is_daily_capped"):
        text += " 하루 최대 요금이 적용됐어요."
    return text


def policy_text(p: dict) -> str:
    return (f"처음 {p['base_minutes']}분은 {won(p['base_fee'])}, 이후 {p['unit_minutes']}분마다 {won(p['extra_fee'])}, "
            f"하루 최대 {won(p['daily_cap'])}")


FEE_TABLE_TITLE = "주차장별 요금표예요."
FEE_TABLE_TAIL = "내 차 요금은 차량 번호와 함께 물어봐 주세요."


def fee_rule(policies: dict | None = None, lot_id: str | None = None, table: bool = False) -> str:
    """주차장별 요금 정책. 요금표는 서버(/rate-policies) 또는 목업 설정(lots.json)에서만 온다.
    table: 주차장마다 한 줄씩 기본·추가·하루 최대 요금을 모두 적은 요금표('주차요금 안내해줘')."""
    if not policies:
        return "요금 정책을 확인할 수 없어요."
    if lot_id:
        policy = policies.get(lot_id)
        return f"{config.catalog.name(lot_id)}: {policy_text(policy)}이에요." if policy else "해당 주차장의 요금 정책을 확인할 수 없어요."
    if table:
        rows = [f"· {config.catalog.name(k)}: {policy_text(p)}" for k, p in policies.items()]
        return "\n".join([FEE_TABLE_TITLE, *rows, FEE_TABLE_TAIL])
    if len({p["base_minutes"] for p in policies.values()}) > 1:
        return "주차장마다 요금이 달라요. " + " / ".join(
            f"{config.catalog.name(k)} 기본 {p['base_minutes']}분 {won(p['base_fee'])}" for k,p in policies.items()) + "."
    lines = [f"{config.catalog.name(k)} 기본 {won(p['base_fee'])}" for k, p in policies.items()]
    return "주차장마다 요금이 달라요. " + " / ".join(lines) + " (기본 " + str(next(iter(policies.values()))["base_minutes"]) + "분)."


def pay(r: dict, no: str) -> str:
    s = r["status"]
    if s == "conflict":
        return "결제 요청이 겹쳤어요. 잠시 후 다시 시도해 주세요."
    if s == "payment_conflict":
        return "다른 결제가 처리 중이에요. 잠시 후 결제 상태를 확인해 주세요."
    if s == "invalid_state":
        return "이미 처리된 결제예요."
    if s == "quote_missing":
        return "요금 견적을 확인할 수 없어 결제할 수 없어요. 요금을 다시 조회해 주세요."
    if s in {"error", "unavailable"}:
        return "결제를 처리하지 못했어요. 지금은 확인할 수 없어요."
    if r.get("idempotent_replayed"):
        return f"이미 처리된 결제예요 ({won(r['amount'])})."
    if s == "already_paid" and not r.get("paid_at"):
        return "이미 결제했어요. 출구 개방 여부는 현장에서 확인해 주세요."
    if s == "paid":
        return f"{won(r['amount'])} 결제됐어요. 출구 개방 여부는 현장에서 확인해 주세요. (모의 결제)"
    if s == "free":
        return "무료 시간이라 결제할 금액이 없어요. 그대로 나가시면 돼요."
    if s == "already_paid":
        return f"이미 결제했어요 ({hhmm(r['paid_at'])}). 출구 개방 여부는 현장에서 확인해 주세요."
    return CAR_NOT_FOUND.format(no=no)


QUOTE_REFRESHED = "요금이 갱신되었어요. {amount}을 다시 확인해 주세요."


ADMIN_CALLED = "관리자를 불렀어요. 상황은 제가 정리해서 전달했으니 다시 설명하지 않으셔도 돼요."

NO_ALERT = "현재 경보는 없어요."
OUT_OF_SCOPE = ("그 내용은 제가 안내할 수 있는 범위가 아니에요. 저는 주차장 빈자리, 내 차 위치, 요금·모의 결제, "
                "모의 화재 경보를 도와드려요. 아래 버튼을 누르거나 편하게 물어보세요.")
ALERT_UNAVAILABLE = "현재 경보 상태를 확인할 수 없어요. 연결 상태를 확인한 뒤 다시 조회해 주세요."


def data_notes(text, evidence):
    """현재 답의 근거에만 시연 표시를 한 번 붙인다(목업 헤더 또는 is_mock)."""
    if not isinstance(evidence, dict):
        return text
    if (evidence.get("data_source") == "mock" or evidence.get("is_mock") is True) and "※ 시연용 데이터예요" not in text:
        text += "\n※ 시연용 데이터예요"
    return text


def alert_location(a):
    lot, spot = a.get("lot_id"), a.get("spot_no")
    if lot is None:
        return "주차장 위치 확인 중"
    name = config.catalog.name(lot)
    label = a.get("space_id") or config.catalog.space_label(lot, spot)
    return f"{name}(정확한 칸 확인 중)" if spot is None or label is None else f"{name} {label} 부근"


def api_alert(a):
    if not a.get("level"):
        return "해당 모의 화재 경보는 해제됐어요." if a.get("resolved_transition") else "현재 모의 화재 경보는 없어요."
    time_text = ""
    try:
        dt = datetime.fromisoformat(a.get("started_at"))
        if dt.tzinfo is not None:
            time_text = " (" + dt.astimezone(config.KST).strftime("%H:%M") + ")"
    except (TypeError, ValueError):
        pass
    text = f"{alert_location(a)}에서 모의 화재(LED) 경보가 발생했어요{time_text}. 실제 화재가 아닌 시연용 경보예요."
    if a.get("source") in C.MOCK_SOURCES:
        text += " (모의 이벤트)"
    if a.get("manager_checking"):
        text += " 관리자가 확인 중이에요."
    if a["level"] >= 2:
        text += "\n" + safety_guidance(a)
    return text


def alert_status(a: dict) -> str:
    where = f"{place(a['lot_id'], a['spot_no'])} 부근"
    if a["level"] == 1:
        return f"{where}에서 이상 신호를 확인하고 있어요. 아직 경보는 아니에요."
    return f"{where}에서 모의 화재(LED)가 감지됐어요.\n" + safety_guidance(a)


def safety_guidance(a):
    exit_name = (config.catalog.lot(a.get("lot_id")) or {}).get("exit", "지정 출구")
    return f"안에 계시면 {exit_name}로, 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요."


def safety_card(a):
    return {"title": "사람 먼저 · 모의 화재(LED) 안내", "guidance": safety_guidance(a),
            "vehicle_help": "차량 피해 확인은 현장 관리자에게 요청해 주세요. 관리자 호출로 차량 정보와 문의를 남길 수 있어요."}


def alert_near(a: dict, car: dict | None) -> str:
    if a.get("api_result") and (a.get("lot_id") is None or a.get("spot_no") is None):
        return alert_location(a) + " · 해당 주차장 전체를 위험 구역으로 보고 접근하지 마세요.\n" + safety_guidance(a)
    if car is None:
        location = "차 위치를 찾지 못했어요."
    elif car["lot_id"] == a["lot_id"]:
        near = "바로 근처" if config.catalog.is_near(car["lot_id"], a["spot_no"], car["spot_no"]) else "같은 주차장"
        location = f"네, {near}예요 ({place(car['lot_id'], car['spot_no'], car.get('space_id'))})."
    else:
        location = f"차는 {config.catalog.name(car['lot_id'])}에 있어요. 경보 해제 전까지 {config.catalog.name(a['lot_id'])}에는 들어오지 마세요."
    return location + "\n" + safety_guidance(a)


EVACUATE = "안에 계시면 {exit}로, 밖이면 들어오지 마세요.\n차를 옮기려 하지 마세요. (시연용 모의 안내)"
EVACUATE_NONE = "현재 경보는 없어요. 비상시에는 가장 가까운 출구로 이동하세요."


def banner(a: dict) -> dict | None:
    if a and C.alerts_source(a) == "not_provided" and not a.get("level"):
        return None
    if a and a.get("unavailable"):
        return {"level": max(1, a.get("level", 0)), "text": ALERT_UNAVAILABLE}
    if a and a.get("stale"):
        return {"level": max(1, a.get("level", 0)), "text": "경보 정보 갱신 지연 · 마지막 확인 상태입니다."}
    if not a or a.get("level", 0) == 0:
        return None
    if a.get("api_result"):
        return {"level": a["level"], "text": data_notes(api_alert(a), a)}
    where = place(a['lot_id'], a['spot_no'])
    if a["level"] == 1:
        return {"level": 1, "text": f"{where} 부근 이상 신호 확인 중"}
    return {"level": 2, "text": f"{where} 모의 화재(LED) 감지 · 안에 계시면 지정 출구로, 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요."}






def where(lot: str | None) -> str:
    return config.catalog.name(lot) if lot else "어느 주차장이든"


ALERT_PUSH_1 ="알려드려요. {lot} {spot} 부근에서 이상 신호를 확인하고 있어요."
ALERT_PUSH_2 = "경보예요. {lot} {spot} 부근 모의 화재(LED) 감지.\n안에 계시면 {exit}로, 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요."


def violations(alert: dict | None) -> str:
    """전용 칸 위반 경보 안내. 경보 API가 없으면 확인할 수 없다고 답한다."""
    if not alert or alert.get("unavailable") or not alert.get("api_result"):
        return "위반 차량 정보는 아직 확인할 수 없어요."
    rows = alert.get("violations") or []
    if not rows:
        return "확인된 위반 차량은 없어요."
    first = rows[0]
    zone = {"ev": "전기차", "accessible": "장애인"}.get(first.get("space_type") or config.catalog.space_zone(first.get("lot_id"), first.get("space_id")), "전용")
    text = f"{place(first['lot_id'], first.get('spot_no'), first.get('space_id'))} {zone} 전용 칸에 일반 차량이 주차돼 있어요."
    if len(rows) > 1:
        text += f" 외 {len(rows) - 1}건이 더 있어요."
    return text
ALERT_CLEARED = "경보가 해제됐어요. 운영 재개 여부는 관리자 안내를 확인해 주세요."


def fee_unavailable(result):
    if result.get("reason") == "pending":
        return "입차 확인 중이라 아직 요금을 안내할 수 없어요. 잠시 후 다시 확인해 주세요."
    if result.get("reason") == "session" or result.get("error_code") == "SESSION_NOT_FOUND":
        return "아직 요금 정보를 확인할 수 없어요."
    if result.get("error_code") in ("VEHICLE_NOT_FOUND", "PARKING_LOT_NOT_FOUND"):
        return "찾을 수 없어요. 번호를 확인해 주세요."
    if result.get("http_status") == 404:
        return "요금 조회 기능이 아직 연결되지 않았어요. 현재는 확인할 수 없어요."
    return "현재 요금 정보를 확인하지 못했어요. 잠시 후 다시 조회해 주세요."

# Reuse the existing safe failure reply when the uncertainty registry is full.
PAYMENT_CAPACITY = "이전 결제의 처리 결과가 불확실하여 추가 결제를 중지했어요. 관리자에게 확인해 주세요."

REQUEST_UNAVAILABLE = "현재 정보를 확인하지 못했어요. 요청은 자동 재시도하지 않습니다. 잠시 후 다시 조회해 주세요."
