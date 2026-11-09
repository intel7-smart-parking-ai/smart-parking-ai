"""Parking and zone queries from one request-scoped occupancy snapshot."""
from .. import config, dialog_context as DC, info_gaps, insights as I, navigation as N
from ..contracts import alerts_source


SPACE_STATE_TEXT = {"empty": "비어 있어요", "occupied": "사용 중이에요", "uncertain": "카메라로 확인 중이에요",
                    "unavailable": "지금 이용할 수 없는 칸이에요"}


def _space_answer(s, p, snapshot):
    """'개발원주차장 B-06 비었어?' 같은 칸 하나 질문. 칸 이름만으로는 주차장을 정하지 않는다(D-04)."""
    label, lot = p["space_id"], p.get("lot_id")
    if not lot or lot == "ambiguous":
        lots = [k for k in config.catalog.ids() if label in config.catalog.spaces(k)]
        if not lots:
            return f"{label} 칸을 찾을 수 없어요. 칸 번호를 확인해 주세요.", None, ["empty_all"], None
        return f"어느 주차장의 {label}인가요?", None, ["lot_" + k for k in lots][:4], None
    if lot not in snapshot["lots"]:
        return "해당 주차장 정보가 아직 연결되지 않았어요.", None, ["empty_all"], snapshot.get("updated_at")
    state = (snapshot.get("space_states") or {}).get(lot, {}).get(label)
    if state is None:
        if label not in config.catalog.spaces(lot):
            return f"{config.catalog.name(lot)}에는 {label} 칸이 없어요.", None, ["lot_" + lot, "empty_all"], snapshot.get("updated_at")
        return f"{config.catalog.name(lot)} {label} 칸 상태는 지금 확인할 수 없어요.", None, ["lot_" + lot, "empty_all"], snapshot.get("updated_at")
    zone = config.catalog.space_zone(lot, label)
    special = {"ev": " (전기차 전용 칸)", "accessible": " (장애인 전용 칸)"}.get(zone, "")
    return f"{config.catalog.name(lot)} {label}은 {SPACE_STATE_TEXT[state]}.{special}", None, ["lot_" + lot, "empty_all"], snapshot.get("updated_at")


def _insight_answer(s, p, snapshot, alert):
    """데이터 집계형 질문(insights.py). 해당 없으면 None."""
    lot, zone = p.get("lot_id"), p.get("zone")
    if lot == "ambiguous":
        return None
    if p.get("section") and lot:
        text, chips, derived = I.section(snapshot, alert, lot, p["section"])
        return text, _with_derived(N.card(snapshot, alert, lot), derived), chips, snapshot["updated_at"]
    if p.get("rank") == "compare" and p.get("lots"):
        text, focus, chips = I.compare(snapshot, alert, p["lots"], zone)
        DC.remember(s, focus, offered_lots=[] if focus else p["lots"])
        return text, N.card(snapshot, alert, focus, zone), chips, snapshot["updated_at"]
    if p.get("rank") == "most":
        if lot and not zone:
            text, chips, derived = I.section_rank(snapshot, alert, lot)
            return text, _with_derived(N.card(snapshot, alert, lot), derived), chips, snapshot["updated_at"]
        text, focus, tied, chips = I.most(snapshot, alert, zone)
        DC.remember(s, focus, offered_lots=tied)
        return text, N.card(snapshot, alert, focus, zone), chips, snapshot["updated_at"]
    if p.get("occupancy"):
        text, chips, derived = I.occupancy(snapshot, alert, lot, zone)
        return text, _with_derived(N.card(snapshot, alert, lot, zone), derived), chips, snapshot["updated_at"]
    return None


def _with_derived(card, derived):
    """계산한 숫자(합계·구역별)를 카드 근거로 남긴다. 기존 카드 키는 그대로이고 키 하나만 더한다."""
    if derived:
        card["data"]["derived_counts"] = derived
    return card


def empty_spots(s, p, snapshot, alert, *, api):
    if p.get("space_id") and not p.get("zone"):
        return _space_answer(s, p, snapshot)
    answer = _insight_answer(s, p, snapshot, alert)
    if answer is not None:
        return answer
    if not p.get("lot_id"):
        # '어느 주차장?' 다음 턴의 '공항'·'세 번째' 선택을 위해 제시한 순서를 기억한다.
        DC.remember(s, offered_lots=N.order_lots(snapshot))
    text, card, chips, updated = N.query(snapshot, alert, p)
    if api and alert.get("unavailable") and alerts_source(alert) != "not_provided" and (not p.get("lot_id") or p["lot_id"] in snapshot["lots"]):
        selected = snapshot["lots"].items() if not p.get("lot_id") else [(p["lot_id"], snapshot["lots"][p["lot_id"]])]
        text = " / ".join(f"{config.catalog.name(k)} 빈자리 {N.count(v, p.get('zone')) if N.count(v, p.get('zone')) is not None else '미확인'}자리, 확인 중 {v.get('uncertain', 0)}칸" for k, v in selected)
        text += "이에요."
        chips = ["empty_all", "call_admin"]
    if p.get("vacancy_notice"):
        text = "자리가 나면 알려드리는 기능은 없어요.\n" + text.replace("\n", " ")
    if p.get("info_gap"):
        note, tail = info_gaps.prefix(p), info_gaps.suffix(p)
        if note:
            text = f"{note} {text}" if note.endswith("기준으로") else f"{note}\n{text}"
        if tail:
            text = f"{text}\n{tail}"
    selected_lots = [p["lot_id"]] if p.get("lot_id") else snapshot["lots"]
    if p.get("zone") and any(snapshot["lots"].get(k, {}).get("zone_partial") for k in selected_lots):
        text += "\n구역 정보가 일부 칸만 설정돼 있어요"
    return text, card, chips, updated
