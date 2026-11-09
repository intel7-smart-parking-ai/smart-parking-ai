import datetime
from typing import Dict, Any, List, Optional
from .catalog import LOTS_CONFIG

DEFAULT_CHIPS = [
    {"id": "empty_all", "label": "🅿️ 전 주차장 빈자리 확인"},
    {"id": "ev_status", "label": "⚡ 전기차 충전 현황"},
    {"id": "find_car", "label": "🚗 내 차 어디 있어?"},
    {"id": "fee_rule", "label": "💳 주차 요금 안내"},
    {"id": "call_admin", "label": "📞 관리자 호출"}
]

def format_currency(val: int) -> str:
    return f"{val:,}원"

def empty_spots_template(data: Dict[str, Any], lot_id: Optional[str] = None, rank: Optional[str] = None, zone: Optional[str] = None) -> Dict[str, Any]:
    all_lots = data.get("all", {})
    target = data.get("target")
    
    # 최상급 또는 비교 요청인 경우
    if rank == "most":
        if zone == "accessible":
            sorted_lots = sorted(all_lots.values(), key=lambda x: x["accessible_empty"], reverse=True)
            best = sorted_lots[0]
            text = (
                f"지금 장애인 구역 빈자리는 **{best['name']}**이 {best['accessible_empty']}자리로 가장 많아요 "
                f"({best['accessible_capacity']}칸 중 {round(best['accessible_empty']/max(1,best['accessible_capacity'])*100)}% 여유).\n"
                f"다음은 " + " · ".join([f"{l['name']} {l['accessible_empty']}자리" for l in sorted_lots[1:]]) + " 순이에요."
            )
        elif zone == "ev":
            sorted_lots = sorted(all_lots.values(), key=lambda x: x["ev_empty"], reverse=True)
            best = sorted_lots[0]
            text = (
                f"지금 전기차 충전 구역 빈자리는 **{best['name']}**이 {best['ev_empty']}자리로 가장 많아요.\n"
                f"다음은 " + " · ".join([f"{l['name']} {l['ev_empty']}자리" for l in sorted_lots[1:]]) + " 순이에요."
            )
        else:
            sorted_lots = sorted(all_lots.values(), key=lambda x: x["total_empty"], reverse=True)
            best = sorted_lots[0]
            text = (
                f"현재 빈자리가 가장 많은 곳은 **{best['name']}**으로 총 **{best['total_empty']}면** 남아있어요!\n"
                f"• 전체 주차장 현황:\n" + "\n".join([f"  - {l['name']}: {l['total_empty']}면 (일반 {l['general_empty']}, 전기 {l['ev_empty']}, 장애인 {l['accessible_empty']})" for l in sorted_lots])
            )
        card = {
            "type": "empty_spots",
            "data": {
                "highlight_lot": best["lot_id"],
                "lots": list(all_lots.values()),
                "zone_filter": zone or "all"
            }
        }
        chips = [
            {"id": f"lot:{best['lot_id']}", "label": f"{best['name']} 자세히 보기"},
            {"id": "zone:ev", "label": "전기차 충전소 보기"},
            {"id": "fee_rule", "label": "요금 확인하기"}
        ]
        return {"text": text, "card": card, "chips": chips}

    # 특정 주차장 지정된 경우
    if target:
        name = target["name"]
        text = (
            f"**{name}**에는 현재 총 **{target['total_empty']}자리**가 비어있어요.\n"
            f"• 🚗 일반: {target['general_empty']} / {target['general_capacity']}면\n"
            f"• ⚡ 전기차 충전: {target['ev_empty']} / {target['ev_capacity']}면\n"
            f"• ♿ 장애인 전용: {target['accessible_empty']} / {target['accessible_capacity']}면\n"
            f"현재 혼잡도(점유율)는 {target['occupancy_rate']}%입니다."
        )
        card = {
            "type": "empty_spots",
            "data": {
                "lot": target,
                "zone_filter": zone or "all"
            }
        }
        chips = [
            {"id": f"view_map:{target['lot_id']}", "label": f"{name} 도면 확인"},
            {"id": f"fee_lot:{target['lot_id']}", "label": f"{name} 요금 규정"},
            {"id": "empty_all", "label": "다른 주차장 빈자리"}
        ]
        return {"text": text, "card": card, "chips": chips}

    # 전체 주차장 현황 (4개 주차장 빈자리)
    total_spots = sum(l["total_empty"] for l in all_lots.values())
    text = (
        f"**전체 4개 주차장 실시간 빈자리 현황** (총 {total_spots}면 여유) 🅿️\n\n" +
        "\n".join([
            f"• **{l['name']}**: 빈자리 **{l['total_empty']}면** (일반 {l['general_empty']} · 전기 {l['ev_empty']} · 장애인 {l['accessible_empty']})"
            for l in all_lots.values()
        ]) +
        "\n\n원하시는 주차장의 **[도면 확인]** 버튼을 누르시면 실시간 주차 도면으로 즉시 이동합니다."
    )
    card = {
        "type": "empty_spots",
        "data": {
            "lots": list(all_lots.values()),
            "zone_filter": zone or "all",
            "show_map_buttons": True
        }
    }
    chips = [
        {"id": "view_map:A", "label": "🗺️ 개발원 도면 확인"},
        {"id": "view_map:B", "label": "🗺️ 부산역 도면 확인"},
        {"id": "view_map:C", "label": "🗺️ 김해공항 도면 확인"},
        {"id": "view_map:D", "label": "🗺️ 신세계 도면 확인"},
        {"id": "ev_status", "label": "⚡ 전기차 충전 현황"}
    ]
    return {"text": text, "card": card, "chips": chips}

def car_location_template(car: Optional[Dict[str, Any]], car_id: str) -> Dict[str, Any]:
    if not car:
        return {
            "text": f"입력하신 차량 번호({car_id})의 입차 기록을 찾지 못했어요. 번호(예: 23번, 1724)를 다시 한 번 확인해주세요.",
            "card": None,
            "chips": [
                {"id": "find_car_23", "label": "23번 차량 찾기"},
                {"id": "find_car_1724", "label": "1724번 차량 찾기"},
                {"id": "call_admin", "label": "관리자 문의"}
            ]
        }
        
    lot_name = car["lot_name"]
    space_id = car["space_id"]
    parked_min = car["parked_minutes"]
    
    text = (
        f"**{car['plate_number']}** 차량은 현재 **{lot_name} [{space_id}]**에 주차되어 있어요.\n"
        f"• 입차 시각: {car['entry_time']}\n"
        f"• 경과 시간: 약 {parked_min}분째 주차 중입니다."
    )
    card = {
        "type": "car_location",
        "data": car
    }
    chips = [
        {"id": f"fee_car:{car['car_id']}", "label": f"{car['car_id']}번 주차 요금 확인"},
        {"id": f"pay_car:{car['car_id']}", "label": "사전 모의 결제"},
        {"id": "exit_help", "label": "출구 경로 안내"}
    ]
    return {"text": text, "card": card, "chips": chips}

def fee_template(fee_info: Optional[Dict[str, Any]], car_id: str) -> Dict[str, Any]:
    if not fee_info:
        return {
            "text": f"차량({car_id})의 주차 기록이 없어 요금을 조회할 수 없습니다. 번호를 확인해주세요.",
            "card": None,
            "chips": [{"id": "fee_rule", "label": "주차장별 요금표 보기"}]
        }
        
    if fee_info.get("paid"):
        text = (
            f"**{fee_info['plate_number']}** 차량은 이미 정산이 완료되었습니다! 🎉\n"
            f"정산 후 {fee_info['grace_period_min']}분 이내에 출차하시면 추가 요금이 발생하지 않습니다."
        )
        card = {
            "type": "fee",
            "data": fee_info
        }
        chips = [{"id": "exit_help", "label": "출구 안내"}, {"id": "empty_all", "label": "주차장 현황"}]
        return {"text": text, "card": card, "chips": chips}
        
    amt = fee_info["total_fee"]
    amt_str = format_currency(amt)
    text = (
        f"**{fee_info['plate_number']}** 차량의 현재 주차 요금은 **{amt_str}**입니다.\n"
        f"• 주차 장소: {fee_info['lot_name']} ({fee_info['space_id']})\n"
        f"• 주차 시간: {fee_info['parked_minutes']}분 ({fee_info['entry_time']} 입차)\n"
    )
    if fee_info.get("discount", 0) > 0:
        text += f"• 감면 혜택: {fee_info['discount_reason']} (-{format_currency(fee_info['discount'])})\n"
        
    text += f"\n모의 결제를 진행하시겠습니까?"
    card = {
        "type": "fee",
        "data": fee_info
    }
    chips = [
        {"id": f"pay_car:{fee_info['car_id']}", "label": f"{amt_str} 바로 결제하기"},
        {"id": "fee_rule", "label": "요금 부과 기준 보기"},
        {"id": "cancel_action", "label": "나중에 결제"}
    ]
    return {"text": text, "card": card, "chips": chips}

def fee_rule_template(lot_id: Optional[str] = None) -> Dict[str, Any]:
    if lot_id and lot_id in LOTS_CONFIG:
        lot = LOTS_CONFIG[lot_id]
        rule = lot["fee_rule"]
        text = (
            f"**{lot['name']} 주차 요금 안내** 💳\n\n"
            f"• 무료 회차: {rule['free_min']}분 이내 무료\n"
            f"• 기본 요금: 최초 {rule['base_min']}분 {format_currency(rule['base_fee'])}\n"
            f"• 추가 요금: {rule['unit_min']}분당 {format_currency(rule['unit_fee'])}\n"
            f"• 일일 최대: {format_currency(rule['daily_max'])}\n\n"
            f"※ 친환경 전기차 및 장애인 등록 차량은 50% 자동 감면됩니다."
        )
        card = {
            "type": "fee_rule",
            "data": {
                "lots": [
                    {
                        "lot_id": lot["lot_id"],
                        "name": lot["name"],
                        "base_min": rule["base_min"],
                        "base_fee": rule["base_fee"],
                        "unit_min": rule["unit_min"],
                        "unit_fee": rule["unit_fee"],
                        "daily_max": rule["daily_max"],
                        "free_min": rule["free_min"]
                    }
                ]
            }
        }
        chips = [
            {"id": "fee_rule", "label": "💳 전체 4개 주차장 요금표"},
            {"id": f"view_map:{lot_id}", "label": f"🗺️ {lot['name']} 도면 확인"},
            {"id": "empty_all", "label": "🅿️ 빈자리 확인하기"}
        ]
        return {"text": text, "card": card, "chips": chips}
    else:
        text = (
            f"**전체 4개 주차장 요금 안내** 💳\n\n" +
            "\n\n".join([
                f"• **{l['name']}**\n"
                f"  - 기본 요금: 최초 {l['fee_rule']['base_min']}분 {format_currency(l['fee_rule']['base_fee'])}\n"
                f"  - 추가 요금: {l['fee_rule']['unit_min']}분당 {format_currency(l['fee_rule']['unit_fee'])}\n"
                f"  - 일일 최대: {format_currency(l['fee_rule']['daily_max'])} (회차 {l['fee_rule']['free_min']}분 이내 무료)"
                for l in LOTS_CONFIG.values()
            ]) +
            f"\n\n※ 친환경 전기차 및 장애인 등록 차량은 50% 자동 감면 혜택이 적용됩니다."
        )
        card = {
            "type": "fee_rule",
            "data": {
                "lots": [
                    {
                        "lot_id": l["lot_id"],
                        "name": l["name"],
                        "base_min": l["fee_rule"]["base_min"],
                        "base_fee": l["fee_rule"]["base_fee"],
                        "unit_min": l["fee_rule"]["unit_min"],
                        "unit_fee": l["fee_rule"]["unit_fee"],
                        "daily_max": l["fee_rule"]["daily_max"],
                        "free_min": l["fee_rule"]["free_min"]
                    }
                    for l in LOTS_CONFIG.values()
                ]
            }
        }
        chips = [
            {"id": "fee_car:23", "label": "23번 차량 요금 확인"},
            {"id": "empty_all", "label": "🅿️ 주차장 빈자리 확인"},
            {"id": "ev_status", "label": "⚡ 전기차 충전 현황"}
        ]
        return {"text": text, "card": card, "chips": chips}

def payment_confirm_template(car_id: str, fee_info: Dict[str, Any], token: str) -> Dict[str, Any]:
    amt_str = format_currency(fee_info["total_fee"])
    text = (
        f"**{fee_info['plate_number']}** 차량의 주차 요금 **{amt_str}**을 모의 결제할까요?\n"
        f"아래 [결제 승인] 버튼을 누르면 안전하게 결제가 처리됩니다."
    )
    card = {
        "type": "fee",
        "data": fee_info
    }
    chips = [
        {"id": f"confirm_pay:{token}", "label": "✅ 결제 승인"},
        {"id": "cancel_action", "label": "❌ 결제 취소"}
    ]
    return {"text": text, "card": card, "chips": chips}

def payment_receipt_template(receipt: Dict[str, Any]) -> Dict[str, Any]:
    if not receipt.get("success"):
        return {
            "text": f"결제 처리에 실패했습니다.\n사유: {receipt.get('message', '알 수 없는 오류')}",
            "card": None,
            "chips": [{"id": "call_admin", "label": "관리자 호출"}]
        }
        
    amt_str = format_currency(receipt["amount"])
    text = (
        f"**주차 요금 결제가 정상적으로 완료되었습니다!** ✨\n\n"
        f"• 승인 번호: {receipt['payment_id']}\n"
        f"• 결제 금액: {amt_str}\n"
        f"• 결제 시각: {receipt['payment_time']}\n"
        f"• 주차장: {receipt['lot_name']}\n\n"
        f"💡 **출차 안내**: 정산 후 **{receipt['exit_deadline']} (15분 이내)**까지 출차하시면 추가 요금이 발생하지 않습니다. 안전운전 하세요!"
    )
    card = {
        "type": "payment",
        "data": receipt
    }
    chips = [
        {"id": "exit_help", "label": "출구 경로 보기"},
        {"id": "empty_all", "label": "주차장 현황으로"}
    ]
    return {"text": text, "card": card, "chips": chips}

def alert_template(alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if not alerts:
        return {
            "text": "현재 시스템에 등록된 화재나 비상 경보는 없습니다. 모든 구역이 안전하게 정상 운영 중입니다. 안심하시고 이용하세요. 🛡️",
            "card": None,
            "chips": [
                {"id": "empty_all", "label": "🅿️ 주차장 빈자리 확인"},
                {"id": "fee_rule", "label": "주차 요금 안내"}
            ]
        }
        
    a = alerts[0]
    if "A-05" in str(a.get("space_id", "")):
        a["space_id"] = "A-05구역"

    # 체계적인 긴급 대피요령 4대 수칙
    evacuation_steps = [
        {"step": "1단계", "title": "비상 유도등 확인", "desc": "초록색 달리는 사람 비상 유도등을 따라 양측 비상구로 신속히 이동하세요."},
        {"step": "2단계", "title": "호흡기 보호", "desc": "젖은 손수건이나 옷으로 코와 입을 감싸고, 자세를 최대한 낮추어 이동하세요."},
        {"step": "3단계", "title": "승강기 탑승 금지", "desc": "정전 및 질식 위험이 있으니 엘리베이터는 절대 금지, 비상 계단을 이용하세요."},
        {"step": "4단계", "title": "차단기 자동 개방", "desc": "출차 차단기가 자동 비상 개방되었습니다. 차량 탑승을 멈추고 신속히 보행 탈출하세요."}
    ]
    a["evacuation_steps"] = evacuation_steps

    text = (
        f"🚨 **[모의 화재(LED) 비상 대피 경보 발령]** 🚨\n\n"
        f"현재 감지된 신호는 실제 화재가 아닌 시연용 **모의 화재(LED) 경보** 상태입니다.\n"
        f"• **발생 위치**: **{a['lot_name']} [{a.get('space_id', 'A-05구역')}]**\n"
        f"• **경보 내용**: {a.get('message', '모의 화재(LED) 연기 감지')} (경보 레벨 {a.get('level', 2)})\n"
        f"• **감지 시각**: {a.get('detected_at', now_str)}\n\n"
        f"⚠️ **[현장 비상 대피 행동 요령]**:\n"
        f"1. 초록색 비상 유도등을 따라 양측 비상구로 신속히 대피하세요.\n"
        f"2. 젖은 수건이나 옷으로 코와 입을 막고 낮은 자세를 유지하세요.\n"
        f"3. 엘리베이터는 절대 타지 마시고 비상 계단을 이용하세요.\n"
        f"4. 무인 차단기는 자동 개방되었습니다. 안전에 유의하여 즉시 탈출하세요."
    )
    card = {
        "type": "alert",
        "data": a
    }
    chips = [
        {"id": f"view_map:{a.get('lot_id', 'A')}", "label": "🗺️ 화재 위치 도면 확인"},
        {"id": "fire_clear", "label": "🧯 경보 해제 (상황 종료)"},
        {"id": "call_admin", "label": "📞 상황실/119 긴급 신고"},
        {"id": "empty_all", "label": "주차장 현황 보기"}
    ]
    return {"text": text, "card": card, "chips": chips}

def alert_resolved_template(lot_id: str = "A", space_id: str = "A-05구역") -> Dict[str, Any]:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lot_name = LOTS_CONFIG.get(lot_id, {}).get("name", "개발원 주차장")

    safety_checks = [
        {"title": "화재 완전 진압", "desc": "소방대 현장 출동 및 발화점 완전 진압 완료"},
        {"title": "유독가스 배출 완료", "desc": "강제 배연 설비 가동으로 실내 공기질 정상화 확인"},
        {"title": "시설물 정밀 안전 점검", "desc": "소방·전기 시스템 및 승강기 가동 승인"},
        {"title": "정상 주차 관제 복구", "desc": "차단기 및 무인 정산 시스템 정상 운영 재개"}
    ]

    text = (
        f"✅ **[화재 비상 경보 상황 종료 및 해제]** ✅\n\n"
        f"• **안전 확인 구역**: **{lot_name} [{space_id}]**\n"
        f"• **해제 시각**: {now_str}\n"
        f"• **조치 결과**: 소방서 및 자위소방대 발화점 완전 진압 / 연기 및 유독가스 배연 완료\n"
        f"• **안전 점검**: 소방·전기·승강기 시설 안전 점검 합격 (이상 없음)\n"
        f"• **운영 안내**: **주차장 전 구역 정상 운영 재개** (출차 차단기 정상 관제 전환)\n\n"
        f"시설 이용객 여러분께서는 안심하시고 주차장 및 편의시설을 정상적으로 이용하시기 바랍니다. 긴급 상황에 협조해 주셔서 대단히 감사합니다."
    )

    card = {
        "type": "alert_resolved",
        "data": {
            "resolved_id": f"RES-{int(time.time())}",
            "lot_id": lot_id,
            "lot_name": lot_name,
            "space_id": space_id,
            "resolved_at": now_str,
            "status": "safe",
            "safety_checks": safety_checks
        }
    }

    chips = [
        {"id": "empty_all", "label": "🚗 주차장 빈자리 확인"},
        {"id": f"view_map:{lot_id}", "label": "🗺️ 정상 주차 도면 확인"},
        {"id": "zone:ev", "label": "⚡ 전기차 충전 구역"},
        {"id": "fee_rule", "label": "주차 요금 안내"}
    ]
    return {"text": text, "card": card, "chips": chips}

def admin_help_template() -> Dict[str, Any]:
    text = (
        f"**주차장 관리자 호출 서비스 안내** 💁\n\n"
        f"• **호출 비용**: **전액 무료**입니다. 별도의 비용이나 수수료가 전혀 들지 않으니 안심하고 이용하세요.\n"
        f"• **지원 내용**: 주차 위치 안내, 정산 오류 해결, 장애인/임산부 편의 지원, 긴급 배터리 충전 등\n"
        f"• 지금 현장 관리자의 도움이 필요하시면 아래 [관리자 호출] 버튼을 눌러주세요."
    )
    chips = [
        {"id": "call_admin", "label": "📞 관리자 호출하기"},
        {"id": "empty_all", "label": "빈자리 찾기"}
    ]
    return {"text": text, "card": None, "chips": chips}

def call_admin_template(admin_call: Dict[str, Any]) -> Dict[str, Any]:
    text = (
        f"**관리자 호출이 정상 접수되었습니다.** 📞\n\n"
        f"• 접수 번호: **{admin_call['call_id']}**\n"
        f"• 위치: {admin_call['lot_name']} ({admin_call['space_id']})\n"
        f"• 상태: {admin_call['status']}\n\n"
        f"현장 담당자가 3~5분 이내로 신속히 이동하여 도움을 드리겠습니다. 잠시만 차량 근처에서 대기해 주세요."
    )
    card = {
        "type": "admin_call",
        "data": admin_call
    }
    chips = [
        {"id": "call_status", "label": "호출 진행 상태 확인"},
        {"id": "empty_all", "label": "주차장 현황 보기"}
    ]
    return {"text": text, "card": card, "chips": chips}

def exit_help_template(lot_id: Optional[str] = None) -> Dict[str, Any]:
    lot = LOTS_CONFIG.get(lot_id or "A", LOTS_CONFIG["A"])
    text = (
        f"**{lot['name']} 출구 출차 안내** 🚗\n\n"
        f"• 출구 방향: 주차장 바닥의 청색 유도선 및 중앙 통로 출구 화살표를 따라 이동하세요.\n"
        f"• 사전 정산기를 이용하셨다면 정산 후 15분 이내 무정차로 자동 게이트가 열립니다.\n"
        f"• 신세계백화점의 경우 몰 연결 출입구와 백화점 연결 출입구의 동선을 확인해주세요."
    )
    chips = [
        {"id": "fee", "label": "출차 전 요금 정산하기"},
        {"id": "call_admin", "label": "차단기 미개방 문의"}
    ]
    return {"text": text, "card": None, "chips": chips}

def violations_template() -> Dict[str, Any]:
    text = (
        f"**전용구역 주차 점검 현황** 🔍\n\n"
        f"현재 시스템 관측 결과 등록된 불법 주차 또는 전용칸 위반 차량은 확인되지 않았습니다.\n"
        f"• 전기차 충전 구역(완속/급속)과 장애인 전용 구역은 등록 차량만 이용 가능합니다.\n"
        f"• 위반 의심 차량 발견 시 관리자에게 알려주시면 신속히 확인하겠습니다."
    )
    chips = [
        {"id": "call_admin", "label": "위반 의심 차량 신고"},
        {"id": "zone:ev", "label": "충전 구역 현황"}
    ]
    return {"text": text, "card": None, "chips": chips}

def ev_charging_template(chargers: List[Dict[str, Any]], lot_id: Optional[str] = None) -> Dict[str, Any]:
    active_chargers = [c for c in chargers if c["status"] in ("charging", "completed")]
    available_cnt = sum(1 for c in chargers if c["status"] == "available")
    
    text = (
        "⚡ **전기차 충전 구역 실시간 충전 현황** ⚡\n\n"
    )
    for c in active_chargers:
        if c["status"] == "completed":
            text += f"• **{c['lot_name']} [{c['space_id']}]**: {c['plate_number']}차량 — **완충 100% 완료** (출차 대기)\n"
        else:
            text += f"• **{c['lot_name']} [{c['space_id']}]**: {c['plate_number']}차량 — **{c['battery_percent']}% 충전 중** (완충까지 **약 {c['remaining_minutes']}분 남음** / {c['charger_type']})\n"
            
    text += f"\n💡 현재 즉시 이용 가능한 전기차 충전 빈자리는 총 **{available_cnt}면**입니다."

    card = {
        "type": "ev_charging",
        "data": {
            "chargers": chargers,
            "highlight_lot": lot_id
        }
    }
    chips = [
        {"id": "ev_lot:A", "label": "개발원 충전소"},
        {"id": "ev_lot:B", "label": "부산역 충전소"},
        {"id": "ev_lot:C", "label": "김해공항 충전소"},
        {"id": "ev_lot:D", "label": "신세계 충전소"},
        {"id": "empty_all", "label": "주차장 전체 빈자리"}
    ]
    return {"text": text, "card": card, "chips": chips}

def view_map_template(lot_id: str) -> Dict[str, Any]:
    lot = LOTS_CONFIG.get(lot_id or "A", LOTS_CONFIG["A"])
    text = f"🗺️ **{lot['name']} 주차 배치도**를 화면에 표시했습니다! 화면 중앙에서 실시간 빈자리와 전용구역 위치를 확인하실 수 있습니다."
    card = {
        "type": "view_map_trigger",
        "data": {
            "lot_id": lot["lot_id"],
            "lot_name": lot["name"]
        }
    }
    chips = [
        {"id": f"fee_lot:{lot['lot_id']}", "label": f"{lot['name']} 요금표"},
        {"id": "empty_all", "label": "다른 주차장 빈자리"},
        {"id": "ev_status", "label": "전기차 충전 현황"}
    ]
    return {"text": text, "card": card, "chips": chips}

def charging_time_template() -> Dict[str, Any]:
    text = (
        "**전기차 충전 정보 안내** ⚡\n\n"
        "현재 주차 관제 시스템에서는 전기차 충전기 내부의 **실시간 남은 충전 시간 및 배터리 충전율 정보는 연동되지 않아 확인할 수 없어요.**\n\n"
        "대신 현재 각 주차장의 **전기차 충전 구역 빈자리 현황**을 즉시 안내해 드릴 수 있습니다. 빈자리를 확인하시겠어요?"
    )
    chips = [
        {"id": "zone:ev", "label": "⚡ 전기차 충전 구역 빈자리"},
        {"id": "empty_all", "label": "전체 주차장 현황"},
        {"id": "call_admin", "label": "현장 관리자 문의"}
    ]
    return {"text": text, "card": None, "chips": chips}

def nearest_zone_template(lot_id: str, destination: Optional[str] = None, empty_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dest = destination or "국제선"
    
    if lot_id == "C" or dest in ["국제선", "국내선", "화물"]:
        if "국제선" in dest:
            text = (
                "**김해공항 주차장 → 국제선 여객청사 최적 구역 안내** ✈️\n\n"
                "국제선 청사로 가실 때 가장 가까운 주차구역은 **[P2 국제선 구역 (B-01 ~ B-24)]**입니다!\n\n"
                "• **도보 이동**: P2 구역 북측 출구를 통과하면 청사 순환 셔틀 승하차로와 바로 연결되며, **도보 약 1~2분** 만에 국제선 청사 출입구에 도착할 수 있어요.\n"
                "• **팁**: 전기차 충전이 필요하신 경우 B-11, B-12칸에 충전기가 위치해 있습니다."
            )
            highlight_section = "P2"
        elif "국내선" in dest:
            text = (
                "**김해공항 주차장 → 국내선 여객청사 최적 구역 안내** 🛫\n\n"
                "국내선 청사로 가실 때 가장 가까운 주차구역은 **[P1 국내선 구역 (A-01 ~ A-24)]**입니다.\n\n"
                "• 도보 약 1~2분 거리로 국내선 여객청사 출입구와 바로 맞닿아 있습니다."
            )
            highlight_section = "P1"
        else:
            text = (
                "**김해공항 주차장 → 국제화물청사 최적 구역 안내** 📦\n\n"
                "화물청사 방면으로 가실 때는 **[P3 여객·화물 구역 (B-25~36, C-01~04)]**이 가장 가깝습니다."
            )
            highlight_section = "P3"
            
        chips = [
            {"id": "lot:C", "label": "김해공항 실시간 빈자리"},
            {"id": "fee_lot:C", "label": "김해공항 요금표"},
            {"id": "empty_all", "label": "다른 주차장 보기"}
        ]
        card = {
            "type": "empty_spots",
            "data": {
                "lot": empty_data.get("target") if empty_data else None,
                "highlight_section": highlight_section
            }
        }
        return {"text": text, "card": card, "chips": chips}

    # 신세계백화점의 경우
    if "영화관" in dest:
        text = (
            "**신세계백화점 → 영화관 최적 주차구역 안내** 🎬\n\n"
            "영화관으로 이동하실 때는 **[몰(Mall) · A구역 또는 C구역]**에 주차하시는 것이 가장 가깝습니다.\n"
            "상단에 위치한 '영화관 방면 전용 엘리베이터'를 이용하시면 바로 영화관 층으로 연결됩니다."
        )
    else:
        text = (
            "**신세계백화점 최적 주차구역 안내** 🏬\n\n"
            "• **백화점 매장 이용 시**: **B구역 / D구역** (백화점 연결 출입구 & 전용 엘리베이터 인접)\n"
            "• **쇼핑몰 & 영화관 이용 시**: **A구역 / C구역** (몰 연결 출입구 & 에스컬레이터 인접)"
        )
    chips = [
        {"id": "lot:D", "label": "신세계백화점 빈자리"},
        {"id": "fee_lot:D", "label": "신세계 요금 안내"}
    ]
    return {"text": text, "card": None, "chips": chips}

def facility_template(lot_id: str, facility: str = "엘리베이터") -> Dict[str, Any]:
    if lot_id == "D" or facility in ["엘리베이터", "승강기", "에스컬레이터"]:
        text = (
            "**신세계백화점 주차장 편의시설 안내** 🛗\n\n"
            "도면 기준으로 주차장 내 엘리베이터는 **총 2개소**에 위치해 있습니다:\n\n"
            "1. **몰 연결 엘리베이터**: 몰(Mall) A / C구역 상단 (영화관 방면 연결)\n"
            "2. **백화점 연결 엘리베이터**: 백화점 B / D구역 상단 (백화점 매장 바로 연결)\n\n"
            "💡 **추가 편의시설**:\n"
            "• **에스컬레이터**: 하단 몰 연결 출입구(좌측) 및 백화점 연결 출입구(우측)에 각 1개소씩 위치\n"
            "• **비상구 유도등**: 하단 중앙 연결 통로에 위치"
        )
        chips = [
            {"id": "lot:D", "label": "신세계 주차 도면 확인"},
            {"id": "empty_all", "label": "주차장 현황 보기"}
        ]
        return {"text": text, "card": None, "chips": chips}

    text = f"{facility}는 주차장 안내 표지판 및 바닥 유도선을 따라 이동하시면 쉽게 찾으실 수 있습니다."
    return {"text": text, "card": None, "chips": DEFAULT_CHIPS[:3]}

def flight_congestion_template(flight_time: Optional[str] = None, before_hours: Optional[str] = None, empty_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    f_str = flight_time or "비행기 출발"
    b_str = f"{before_hours}시간 전" if before_hours else "출발 3시간 전"
    
    c_lot = empty_data.get("all", {}).get("C", {}) if empty_data else {}
    empty_cnt = c_lot.get("total_empty", 52)
    
    text = (
        f"**김해공항 항공편 탑승 & 주차 혼잡도 안내** ✈️\n\n"
        f"결론부터 말씀드리면, **{f_str} {b_str}에 도착하시면 주차와 탑승 수속 모두 여유 있게 진행하실 수 있습니다!** 전혀 늦지 않고 안전한 일정이에요.\n\n"
        f"• **공항 권장 도착 시간**: 국제선 탑승 시 발권, 수하물 위탁, 보안검색 및 출국심사를 고려해 통상 **출발 2시간 30분 ~ 3시간 전** 도착을 가장 권장합니다.\n"
        f"• **현재 김해공항 주차 현황**: 현재 총 64면 중 **{empty_cnt}면의 빈자리**가 있어 만차 걱정 없이 주차가 가능합니다.\n"
        f"• **추천 팁**: 국제선 탑승 고객은 **[P2 국제선 구역]**에 주차하시면 국제선 청사 출입구까지 도보로 1~2분 만에 가장 빠르게 이동하실 수 있습니다."
    )
    card = {
        "type": "empty_spots",
        "data": {
            "lot": c_lot or {"lot_id": "C", "name": "김해공항 주차장", "total_empty": 52, "total_capacity": 64, "general_empty": 34, "ev_empty": 4, "accessible_empty": 14}
        }
    }
    chips = [
        {"id": "lot:C", "label": "김해공항 P2 구역 빈자리"},
        {"id": "fee_lot:C", "label": "김해공항 주차 요금표"},
        {"id": "call_admin", "label": "관리실 문의"}
    ]
    return {"text": text, "card": card, "chips": chips}

def help_template() -> Dict[str, Any]:
    text = (
        f"안녕하세요! 스마트 주차 안내 챗봇 **PARK:ON BOT**입니다. 🤖\n\n"
        f"다음과 같은 주차장 서비스를 편하게 물어보세요:\n"
        f"1. **빈자리 조회**: '김해공항 자리 있어?', '장애인 자리 제일 많은 곳은?'\n"
        f"2. **내 차 찾기**: '23번 차 어디 있어?', '1724번 차 위치'\n"
        f"3. **주차 요금 & 결제**: '내 차 요금 얼마야?', '요금 규정 알려줘', '모의 결제'\n"
        f"4. **안전 및 대피**: '화재 경보 있어?', '비상 대피로 알려줘'\n"
        f"5. **관리자 지원**: '관리자 부르면 돈 들어?', '관리자 호출해줘'"
    )
    return {"text": text, "card": None, "chips": DEFAULT_CHIPS}

def welcome_template() -> Dict[str, Any]:
    text = (
        "안녕하세요! 스마트 주차 도우미 **PARK:ON 챗봇**입니다. 🚗✨\n\n"
        "실시간 주차장 빈자리 확인부터 내 차 위치 찾기, 주차 요금 조회 및 사전 정산, "
        "전기차 충전 현황까지 편리하게 도와드릴게요!\n\n"
        "궁금하신 점을 편하게 말씀해 주시거나 아래 추천 버튼을 눌러보세요."
    )
    return {"text": text, "card": None, "chips": DEFAULT_CHIPS}

def fallback_template() -> Dict[str, Any]:
    text = (
        "말씀하신 내용을 명확히 확인하지 못했어요. 😢\n"
        "주차장 이름(개발원, 부산역, 김해공항, 신세계)이나 차량 번호(예: 23번, 1724번), "
        "또는 원하시는 서비스를 아래 추천 버튼에서 선택해 주시면 빠르게 안내해 드릴게요!"
    )
    return {"text": text, "card": None, "chips": DEFAULT_CHIPS}

