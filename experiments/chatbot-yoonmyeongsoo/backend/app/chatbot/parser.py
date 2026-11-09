import re
from typing import Dict, Any, Optional, List, Tuple
from .catalog import LOTS_CONFIG, get_lot

KOREAN_NUMBERS = {
    "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9,
    "십": 10, "이십": 20, "삼십": 30, "사십": 40, "오십": 50, "육십": 60, "칠십": 70, "팔십": 80, "구십": 90
}

# Q7 버그 수정 반영: 부르면|불러도|돈\s*들 추가
ADMIN_QUESTION = re.compile(r"(하면|비용|돈이|돈\s*들|부르면|불러도|유료|무료|가능한|되나요|방법|어떻게|계세요|있나요|어디\s*(?:계|있))")

def parse_korean_number(text: str) -> Optional[int]:
    text = text.strip()
    if text.isdigit():
        return int(text)
    
    val = 0
    m = re.match(r"^(이십|삼십|사십|오십|육십|칠십|팔십|구십|십)?([일이삼사오육칠팔구])?$", text)
    if m:
        tens, ones = m.groups()
        if tens:
            val += KOREAN_NUMBERS.get(tens, 0)
        if ones:
            val += KOREAN_NUMBERS.get(ones, 0)
        if val > 0:
            return val
    return None

def normalize_text(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    # 구어체 정규화
    t = re.sub(r"알\s*수\s*있음", "알 수 있어", t)
    t = re.sub(r"잇어|있나|이써|있음", "있어", t)
    t = re.sub(r"얼마양|얼마얌", "얼마야", t)
    t = re.sub(r"알려줭|알려조", "알려줘", t)
    t = re.sub(r"몇개야|몇개얌|몇개임|몇개고", "몇 개야", t)
    return t

def extract_vehicle(text: str) -> Optional[str]:
    m_plate = re.search(r"(\d{2,3}[가-힣]\s*\d{4})", text)
    if m_plate:
        return m_plate.group(1).replace(" ", "")
    
    m_four = re.search(r"(\d{4})\s*(?:번)?", text)
    if m_four:
        return m_four.group(1)
        
    m_num = re.search(r"(\d{1,3})\s*번", text)
    if m_num:
        return m_num.group(1)
        
    m_kor = re.search(r"([일이삼사오육칠팔구십]{1,4})\s*번", text)
    if m_kor:
        parsed = parse_korean_number(m_kor.group(1))
        if parsed is not None:
            return str(parsed)
            
    m_two = re.search(r"\b([1-9]\d)\b", text)
    if m_two:
        return m_two.group(1)
        
    return None

def extract_lot(text: str) -> Optional[str]:
    # 공항 키워드
    if re.search(r"공항|김해|비행기|항공|청사", text):
        return "C"
    # 백화점 키워드
    if re.search(r"신세계|백화점|쇼핑몰|영화관", text):
        return "D"
    # 부산역
    if re.search(r"부산역|역주차장|기차", text):
        return "B"
    # 개발원
    if re.search(r"개발원|본원", text):
        return "A"

    for lot_id, lot in LOTS_CONFIG.items():
        if lot["name"] in text:
            return lot_id
        for alias in lot["aliases"]:
            if re.search(rf"(?:^|\s|[은는이가을를와과]){re.escape(alias)}(?:$|\s|[은는이가을를와과])", text):
                return lot_id
            if alias in text and len(alias) >= 2:
                return lot_id
    return None

def extract_zone(text: str) -> Optional[str]:
    if re.search(r"전기(?:차)?|충전", text):
        return "ev"
    if re.search(r"장애인|복지", text):
        return "accessible"
    if re.search(r"일반", text):
        return "general"
    return None

def extract_space(text: str) -> Optional[str]:
    m = re.search(r"([A-D])-?(\d{1,2})", text, re.IGNORECASE)
    if m:
        sec = m.group(1).upper()
        num = int(m.group(2))
        return f"{sec}-{num:02d}"
    return None

def guarded_intent(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    # 1. 취소 / 중단
    if re.search(r"결제\s*(안\s*할래|취소|하지마|안해)|취소해줘?|그만|종료", text):
        return "cancel_action", {"target": "payment"}
        
    # 2. 관리자 관련 안전 가드 (Q7 버그 패치)
    if "관리자" in text:
        if ADMIN_QUESTION.search(text):
            return "admin_help", {}
        if re.search(r"호출|불러|연결|찾아|와주|와줘|요청", text):
            return "call_admin", {}
        if re.search(r"상태|현황|접수|오고\s*있", text):
            return "call_status", {}
            
    # 3. 결제 문의 가드
    if re.search(r"결제\s*(어떻게|방법|어디서|안내)", text):
        return "payment_help", {}
    if re.search(r"결제\s*(오류|실패|안돼|이상|문제)", text):
        return "payment_issue", {}
        
    # 4. 전기차 충전 현황 / 충전 시간 / 배터리 충전율 문의
    if re.search(r"알\s*수\s*있|확인할\s*수\s*있|알려줄\s*수\s*있", text) and re.search(r"충전\s*(?:시간|량|율|정보|현황)", text):
        return "charging_time_info", {}
    if re.search(r"전기차\s*충전\s*(?:현황|상태|정보)|충전\s*(?:현황|상태)|충전\s*(?:시간|량|율|중인|얼마나)|남은\s*충전\s*시간|완충\s*시간|충전\s*(?:얼마나|언제)|몇\s*퍼센트\s*(?:충전)?", text):
        return "ev_charging_status", {}

    # 5. 전 주차장 / 각 주차장 요금 안내 (전체 4개 주차장 요금)
    if re.search(r"(?:각\s*(?:주차장|곳)?|주차장\s*별|전\s*주차장|전체\s*주차장|모든\s*주차장|4개\s*주차장|주차장\s*4곳|주차장\s*마다|주차장\s*각각).*?(?:요금|주차비|비용|얼마|정산|기준|표)|(?:각\s*주차장|주차장\s*별|4개\s*주차장)\s*요금", text):
        return "fee_rule", {"all_lots": True}

    # 6. 전 주차장 빈자리 확인 (전체 주차장 4곳 현황)
    if re.search(r"전\s*주차장\s*빈자리|전체\s*주차장\s*빈자리|모든\s*주차장\s*빈자리|전\s*주차장|주차장\s*전체\s*빈자리", text):
        return "get_empty_spots", {"all_lots": True}

    # 6. 도면 확인 요청
    if re.search(r"도면\s*(?:확인|보기|보여줘)?|배치도|약도", text):
        return "view_map", {}

    # 7. 화재 경보 해제 / 화재 상황 해결
    if re.search(r"경보\s*(?:해제|종료|꺼졌|취소)|화재\s*(?:해제|종료|진압|완료|꺼졌|해결)|불\s*(?:꺼졌|진압|끝|해결)|상황\s*종료|안전\s*확인", text):
        return "clear_alert", {}

    return None

def parse(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text_norm = normalize_text(text)
    
    # 안전 가드 먼저 실행
    guard = guarded_intent(text_norm)
    if guard:
        intent, params = guard
        if not params.get("all_lots"):
            lot = extract_lot(text_norm)
            if lot: params["lot"] = lot
        car = extract_vehicle(text_norm)
        if car: params["car_id"] = car
        return {"intent": intent, "params": params, "source": "rule"}
        
    lot = extract_lot(text_norm)
    car = extract_vehicle(text_norm)
    zone = extract_zone(text_norm)
    space = extract_space(text_norm)
    
    params: Dict[str, Any] = {}
    if lot: params["lot"] = lot
    if car: params["car_id"] = car
    if zone: params["zone"] = zone
    if space: params["space"] = space
    
    # 컨텍스트 복원
    if context and not car and context.get("car_id"):
        params["car_id"] = context.get("car_id")
    if context and not lot and context.get("lot"):
        params["lot"] = context.get("lot")

    # ========================================================
    # 규칙 기반 우선순위 매칭
    # ========================================================

    # [최초 진입 및 인사말]
    if not text_norm or re.search(r"^(?:시작|start|처음|입장|웰컴|welcome|안녕|안녕하세요|하이|반가워|hello|hi)$", text_norm):
        return {"intent": "welcome", "params": params, "source": "rule"}

    # [이미지 2 해결] 목적지 청사/시설과 가장 가까운 주차구역 안내
    if re.search(r"(국제선|국내선|화물|여객|영화관|쇼핑몰|백화점)\s*(?:청사|터미널|관)?.*(?:가까운|가는\s*길|어디로|어디가|주차구역|구역)", text_norm):
        dest = None
        if "국제선" in text_norm: dest = "국제선"
        elif "국내선" in text_norm: dest = "국내선"
        elif "화물" in text_norm: dest = "화물"
        elif "영화관" in text_norm: dest = "영화관"
        elif "몰" in text_norm or "쇼핑몰" in text_norm: dest = "몰"
        elif "백화점" in text_norm: dest = "백화점"
        params["destination"] = dest
        if not lot:
            params["lot"] = "C" if dest in ["국제선", "국내선", "화물"] else "D"
        return {"intent": "nearest_zone_guide", "params": params, "source": "rule"}

    # [이미지 3 해결] 편의 시설물 문의 (엘리베이터, 에스컬레이터, 영화관 입구 등)
    if re.search(r"(엘리베이터|승강기|에스컬레이터|영화관|비상구|출입구|화장실)", text_norm) and re.search(r"(몇\s*개|어디|위치|있어|알려줘|어떻게)", text_norm):
        facility = "엘리베이터"
        if "에스컬레이터" in text_norm: facility = "에스컬레이터"
        elif "영화관" in text_norm: facility = "영화관"
        elif "비상구" in text_norm: facility = "비상구"
        elif "화장실" in text_norm: facility = "화장실"
        params["facility"] = facility
        if not lot:
            params["lot"] = "D" if "백화점" in text_norm or "신세계" in text_norm else (lot or "D")
        return {"intent": "facility_info", "params": params, "source": "rule"}

    # [이미지 4 해결] 비행기 출발 시간 및 공항 주차장 혼잡도 문의
    if re.search(r"(비행기|항공|출발|탑승)", text_norm) and re.search(r"(시간\s*전|도착|혼잡|밀릴|붐빌|여유|괜찮)", text_norm):
        params["lot"] = "C" # 김해공항 자동 지정
        # 시간 추출 (예: 2시20분, 3시간전)
        m_flight = re.search(r"(\d{1,2}시(?:\s*\d{1,2}분)?)", text_norm)
        m_before = re.search(r"(\d{1,2})\s*시간\s*전", text_norm)
        if m_flight: params["flight_time"] = m_flight.group(1)
        if m_before: params["before_hours"] = m_before.group(1)
        return {"intent": "flight_congestion_info", "params": params, "source": "rule"}

    # 1. 대피 및 안전 경보
    if re.search(r"불|화재|대피|연기|비상|사이렌|경보|위험", text_norm):
        if re.search(r"대피|비상구|출구|어디로", text_norm):
            return {"intent": "evacuation_guide", "params": params, "source": "rule"}
        if re.search(r"내\s*차|근처|주변", text_norm):
            return {"intent": "alert_near_my_car", "params": params, "source": "rule"}
        return {"intent": "get_alerts", "params": params, "source": "rule"}
        
    # 2. 출구 안내
    if re.search(r"나가는\s*길|출구|어디로\s*나가|나갈|출차", text_norm):
        return {"intent": "exit_help", "params": params, "source": "rule"}

    # 3. 전용구역 위반
    if re.search(r"불법주차|위반|부정\s*주차|전용\s*(?:칸|구역)?\s*위반", text_norm):
        return {"intent": "get_violations", "params": params, "source": "rule"}

    # 4. 결제 실행
    if re.search(r"결제(?:해줘|할래|할게|할까|하기|진행|승인|요청)|정산(?:해줘|할래|할게|할까|하기)", text_norm):
        return {"intent": "pay_fee", "params": params, "source": "rule"}

    # 5. 요금 규정 vs 요금 조회
    if re.search(r"요금\s*(표|규정|정책|얼마씩|기준|기본요금)", text_norm):
        if not lot:
            params["all_lots"] = True
            params.pop("lot", None)
        return {"intent": "fee_rule", "params": params, "source": "rule"}
        
    if re.search(r"요금|얼마|비용|정산|주차비", text_norm):
        if car:
            return {"intent": "get_fee", "params": params, "source": "rule"}
        else:
            if not lot:
                params["all_lots"] = True
                params.pop("lot", None)
            return {"intent": "fee_rule", "params": params, "source": "rule"}

    # 6. 내 차 찾기
    if re.search(r"내\s*차|차\s*어디|주차\s*위치|어디\s*(?:주차|대어|뒀|세웠)", text_norm) or (car and re.search(r"어디", text_norm)):
        return {"intent": "find_my_car", "params": params, "source": "rule"}

    # 7. 빈자리 및 분석
    if re.search(r"자리|빈자리|주차\s*할\s*데|남은|여유|만차|혼잡|한산|몇\s*개|현황|주차\s*(?:가능|대도|댈|공간)", text_norm):
        if re.search(r"가장\s*(?:많은|여유|한산)|제일\s*(?:많은|여유|한산)|어디가\s*제일", text_norm):
            params["rank"] = "most"
        elif re.search(r"비교|어디가\s*더|둘\s*중|중에\s*어디", text_norm):
            params["rank"] = "compare"
        elif re.search(r"점유율|얼마나\s*찼|혼잡도", text_norm):
            params["analytics"] = "occupancy"
        return {"intent": "get_empty_spots", "params": params, "source": "rule"}

    # 8. 감사 / 인사
    if re.search(r"고마워|감사|수고|안녕|하이|반가워", text_norm):
        return {"intent": "thanks", "params": params, "source": "rule"}

    # 9. 도움말
    if re.search(r"도움말|사용법|기능|뭐\s*할\s*수|할\s*수\s*있는", text_norm):
        return {"intent": "help", "params": params, "source": "rule"}

    # 10. 차 번호만 단독 입력된 경우
    if car and len(text_norm.strip()) <= 10:
        return {"intent": "find_my_car", "params": params, "source": "rule"}

    # 11. 주차장 이름만 단독 입력된 경우
    if lot and len(text_norm.strip()) <= 10:
        return {"intent": "get_empty_spots", "params": params, "source": "rule"}

    return {"intent": "unknown", "params": params, "source": "rule"}
