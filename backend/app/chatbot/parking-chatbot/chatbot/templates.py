"""답변 문장 템플릿. 말투를 바꾸려면 이 파일만 고치면 된다.

원칙: 결론·숫자 먼저, 두 줄 이내, 쉬운 말("미리 결제", "들어온 시간"),
경보는 실제 화재가 아니라 '모의 화재(LED) 감지'로 표기.
"""
from datetime import datetime
from . import config


def hhmm(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).strftime("%H:%M")


def won(n: int) -> str:
    return f"{n:,}원"


WELCOME = "주차장 안내봇이에요. 빈자리, 내 차 위치, 요금·미리 결제를 도와드려요.\n아래 버튼을 누르거나 편하게 물어보세요."
HELP = "이런 걸 할 수 있어요: 빈자리 확인, 내 차 위치, 요금 확인과 미리 결제, 출차 문제 해결, 관리자 호출.\n아래 버튼을 눌러 보세요."
THANKS = "천만에요. 더 필요한 게 있으면 말씀해 주세요."

ASK_VEHICLE = "차량 번호를 알려주세요. 차 지붕에 붙은 숫자예요. (예: 07)"
ASK_DIFFERENT_VEHICLE = "다른 차량 번호를 알려주세요. 차 지붕의 숫자 01~24 중 하나예요."
INVALID_VEHICLE = "차량 번호는 01부터 24 사이예요. 다시 알려주세요."
CONFIRM_VEHICLE = "{no}번 차 맞으세요?"
VEHICLE_SET = "{no}번 차로 기억해 둘게요."
VEHICLE_SWITCH = "{no}번 차로 바꿔서 볼게요."
VEHICLE_DENIED = "알겠어요. 차량 번호를 다시 알려주세요."
NOTHING_TO_CONFIRM = "확인할 내용이 없어요. 무엇을 도와드릴까요?"
CANCEL_ADMIN = "알겠어요. 새 관리자 호출은 하지 않을게요."
CANCEL_PAYMENT = "새 결제는 진행하지 않을게요."
CANCEL_PENDING = "진행 중이던 요청을 취소했어요."
CANCEL_NONE = "진행 중인 요청은 없어요."
REFUND_UNSUPPORTED = "이미 완료한 결제의 환불은 이 기능에서 처리하지 않습니다."

UNKNOWN_FIRST = "잘 이해하지 못했어요. 아래 버튼에서 원하는 안내를 골라 주세요."
UNKNOWN_AGAIN = "아래에서 골라 주시거나, 관리자를 불러 드릴게요."


def empty_spots(lots: dict, lot_id: str | None) -> str:
    if lot_id:
        n = lots[lot_id]["empty"]
        return f"{lot_id} 주차장에 {n}자리 있어요." if n else f"{lot_id} 주차장은 지금 만차예요."
    best = max(lots, key=lambda k: lots[k]["empty"])
    total = sum(v["empty"] for v in lots.values())
    if total == 0:
        return "지금은 모든 주차장이 만차예요."
    return f"빈자리 {total}개, 가장 여유 있는 곳은 {best} 주차장({lots[best]['empty']}자리)이에요."


def car_location(d: dict) -> str:
    return f"{d['vehicle_no']}번 차는 {config.catalog.name(d['lot_id'])} {d['spot_no']}번 칸에 있어요."


CAR_NOT_FOUND = "{no}번 차의 입차 기록이 없어요. 번호가 맞는지 확인해 주세요."


def fee(d: dict) -> str:
    if d["paid"]:
        return f"{d['vehicle_no']}번 차는 이미 결제했어요."
    if d["fee"] == 0:
        left = d["free_minutes"] - d["minutes"]
        return f"아직 무료 시간이에요. {left}분 안에 나가면 0원이에요."
    return f"지금 나가면 {won(d['fee'])}이에요. 30분 뒤에는 {won(d['fee_after_30'])}이 돼요."


def fee_rule(free_min: int, unit_min: int, per_unit: int) -> str:
    return f"처음 {free_min}분은 무료, 이후 {unit_min}분마다 {won(per_unit)}이에요. (임시 요금 기준)"


def pay(r: dict, no: str) -> str:
    s = r["status"]
    if s == "paid":
        return f"{won(r['amount'])} 결제됐어요. 출구 개방 여부는 현장에서 확인해 주세요. (모의 결제)"
    if s == "free":
        return "무료 시간이라 결제할 금액이 없어요. 그대로 나가시면 돼요."
    if s == "already_paid":
        return f"이미 결제했어요 ({hhmm(r['paid_at'])}). 출구 개방 여부는 현장에서 확인해 주세요."
    return CAR_NOT_FOUND.format(no=no)


def exit_issue(fee_d: dict | None, no: str) -> tuple[str, str]:
    """(원인 코드, 문장)"""
    if fee_d is None:
        return "no_record", f"{no}번 차의 입차 기록이 없어요. 번호가 맞다면 관리자를 불러 드릴게요."
    if fee_d["paid"]:
        return "paid", "결제는 완료됐어요. 그래도 안 열리면 번호 인식 문제일 수 있으니 관리자를 불러 주세요."
    if fee_d["fee"] == 0:
        return "free", "무료 시간이라 바로 나갈 수 있어요. 안 열리면 관리자를 불러 주세요."
    return "unpaid", f"아직 결제 전이에요 ({won(fee_d['fee'])}). 결제 상태를 확인한 뒤에도 출구가 열리지 않으면 관리자를 호출해 주세요."


ADMIN_CALLED = "관리자를 불렀어요. 상황은 제가 정리해서 전달했으니 다시 설명하지 않으셔도 돼요."

NO_ALERT = "현재 경보는 없어요."
ALERT_UNAVAILABLE = "현재 경보 상태를 확인할 수 없어요. 연결 상태를 확인한 뒤 다시 조회해 주세요."


def alert_status(a: dict) -> str:
    where = f"{config.catalog.name(a['lot_id'])} {a['spot_no']}번 칸 부근"
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
    if car is None:
        location = "차 위치를 찾지 못했어요."
    elif car["lot_id"] == a["lot_id"]:
        near = "바로 근처" if abs(car["spot_no"] - a["spot_no"]) <= 1 else "같은 주차장"
        location = f"네, {near}예요 ({config.catalog.name(car['lot_id'])} {car['spot_no']}번 칸)."
    else:
        location = f"차는 {config.catalog.name(car['lot_id'])}에 있어요. 경보 해제 전까지 {config.catalog.name(a['lot_id'])}에는 들어오지 마세요."
    return location + "\n" + safety_guidance(a)


EVACUATE = "안에 계시면 {exit}로, 밖이면 들어오지 마세요.\n차를 옮기려 하지 마세요. (시연용 모의 안내)"
EVACUATE_NONE = "현재 경보는 없어요. 비상시에는 가장 가까운 출구로 이동하세요."


def banner(a: dict) -> dict | None:
    if a and a.get("unavailable"):
        return {"level": 1, "text": ALERT_UNAVAILABLE}
    if a and a.get("stale"):
        return {"level": max(1, a.get("level", 0)), "text": "경보 정보 갱신 지연 · 마지막 확인 상태입니다."}
    if not a or a.get("level", 0) == 0:
        return None
    where = f"{config.catalog.name(a['lot_id'])} {a['spot_no']}번 칸"
    if a["level"] == 1:
        return {"level": 1, "text": f"{where} 부근 이상 신호 확인 중"}
    return {"level": 2, "text": f"{where} 모의 화재(LED) 감지 · 안에 계시면 지정 출구로, 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요."}






def where(lot: str | None) -> str:
    return config.catalog.name(lot) if lot else "어느 주차장이든"


ALERT_PUSH_1 ="알려드려요. {lot} {spot}번 칸 부근에서 이상 신호를 확인하고 있어요."
ALERT_PUSH_2 = "경보예요. {lot} {spot}번 칸 부근 모의 화재(LED) 감지.\n안에 계시면 {exit}로, 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요."
ALERT_CLEARED = "경보가 해제됐어요. 운영 재개 여부는 관리자 안내를 확인해 주세요."
