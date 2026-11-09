import os
import time
import uuid
import datetime
from typing import Dict, Any, List, Optional
import urllib.request
import urllib.error
import json
from .catalog import LOTS_CONFIG, get_lot, get_vehicle_type

PARK_ON_BASE_URL = os.environ.get("PARK_ON_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

class DataAdapter:
    def get_empty_spots(self, lot_id: Optional[str] = None, zone: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError
        
    def find_car(self, car_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
        
    def get_fee(self, car_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
        
    def pay_fee(self, car_id: str, amount: int, confirmation_token: str) -> Dict[str, Any]:
        raise NotImplementedError
        
    def get_alerts(self, lot_id: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError
        
    def call_admin(self, lot_id: str, space_id: Optional[str], reason: str) -> Dict[str, Any]:
        raise NotImplementedError

class SmartAdapter(DataAdapter):
    """실서버(PARK_ON_BASE_URL) 조회 시도 후 실패 시 견고한 Mock 데이터로 fallback"""
    
    def __init__(self):
        # 모의 입차 차량 데이터 (D-54 및 D-57/59 기준)
        now = datetime.datetime.now()
        self.mock_cars = {
            "23": {
                "car_id": "23",
                "plate_number": "12가 3423",
                "lot_id": "A",
                "space_id": "A-05",  # 전기차
                "entry_time": (now - datetime.timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 45,
                "type": "ev"
            },
            "14": {
                "car_id": "14",
                "plate_number": "34나 1214",
                "lot_id": "A",
                "space_id": "B-06",  # 장애인
                "entry_time": (now - datetime.timedelta(minutes=70)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 70,
                "type": "accessible"
            },
            "40": {
                "car_id": "40",
                "plate_number": "56다 7840",
                "lot_id": "A",
                "space_id": "A-01",
                "entry_time": (now - datetime.timedelta(minutes=25)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 25,
                "type": "general"
            },
            "58": {
                "car_id": "58",
                "plate_number": "78라 9058",
                "lot_id": "B",
                "space_id": "A-03",
                "entry_time": (now - datetime.timedelta(minutes=110)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 110,
                "type": "general"
            },
            "57": {
                "car_id": "57",
                "plate_number": "89마 5757",
                "lot_id": "C",
                "space_id": "A-11",  # 전기차 (김해공항 P1)
                "entry_time": (now - datetime.timedelta(minutes=140)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 140,
                "type": "ev"
            },
            "32": {
                "car_id": "32",
                "plate_number": "90바 3232",
                "lot_id": "D",
                "space_id": "A-05",  # 장애인 (신세계 A구역)
                "entry_time": (now - datetime.timedelta(minutes=95)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 95,
                "type": "accessible"
            },
            "1724": {
                "car_id": "1724",
                "plate_number": "17가 1724",
                "lot_id": "D",
                "space_id": "B-03",
                "entry_time": (now - datetime.timedelta(minutes=35)).strftime("%Y-%m-%d %H:%M"),
                "parked_minutes": 35,
                "type": "general"
            }
        }
        
        # 활성 경보 상태 (기본 정상)
        self.active_alerts = []
        
        # 관리자 호출 내역
        self.admin_calls = []
        
        # 결제 완료 내역
        self.paid_records = set()

    def _fetch_json(self, path: str, timeout: float = 1.5) -> Optional[Any]:
        try:
            req = urllib.request.Request(f"{PARK_ON_BASE_URL}{path}", headers={"User-Agent": "ParkOnChatbot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except Exception:
            pass
        return None

    def get_empty_spots(self, lot_id: Optional[str] = None, zone: Optional[str] = None) -> Dict[str, Any]:
        # 4개 주차장 현황 종합
        results = {}
        for lid, lot in LOTS_CONFIG.items():
            cap = lot["capacity"]
            ev_cap = len(lot.get("ev_spaces", []))
            acc_cap = len(lot.get("accessible_spaces", []))
            gen_cap = cap - ev_cap - acc_cap
            
            # 주차 중 차량 수 계산
            occupied_gen = sum(1 for c in self.mock_cars.values() if c["lot_id"] == lid and c["type"] == "general")
            occupied_ev = sum(1 for c in self.mock_cars.values() if c["lot_id"] == lid and c["type"] == "ev")
            occupied_acc = sum(1 for c in self.mock_cars.values() if c["lot_id"] == lid and c["type"] == "accessible")
            
            empty_gen = max(0, gen_cap - occupied_gen)
            empty_ev = max(0, ev_cap - occupied_ev)
            empty_acc = max(0, acc_cap - occupied_acc)
            total_empty = empty_gen + empty_ev + empty_acc
            
            results[lid] = {
                "lot_id": lid,
                "name": lot["name"],
                "total_capacity": cap,
                "total_empty": total_empty,
                "general_empty": empty_gen,
                "general_capacity": gen_cap,
                "ev_empty": empty_ev,
                "ev_capacity": ev_cap,
                "accessible_empty": empty_acc,
                "accessible_capacity": acc_cap,
                "occupancy_rate": round((cap - total_empty) / cap * 100, 1)
            }
            
        if lot_id and lot_id in results:
            return {"target": results[lot_id], "all": results}
        return {"target": None, "all": results}

    def find_car(self, car_id: str) -> Optional[Dict[str, Any]]:
        cid = str(car_id).strip()
        # 직접 매칭
        if cid in self.mock_cars:
            car = self.mock_cars[cid].copy()
            car["lot_name"] = LOTS_CONFIG[car["lot_id"]]["name"]
            return car
        # 끝자리 매칭 (예: 1724 -> 17가 1724)
        for key, car in self.mock_cars.items():
            if cid in car["plate_number"] or key.endswith(cid) or cid.endswith(key):
                res = car.copy()
                res["lot_name"] = LOTS_CONFIG[res["lot_id"]]["name"]
                return res
        return None

    def get_fee(self, car_id: str) -> Optional[Dict[str, Any]]:
        car = self.find_car(car_id)
        if not car:
            return None
            
        lot = LOTS_CONFIG[car["lot_id"]]
        rule = lot["fee_rule"]
        minutes = car["parked_minutes"]
        
        # 이미 결제된 경우
        if car["car_id"] in self.paid_records:
            return {
                "car_id": car["car_id"],
                "plate_number": car["plate_number"],
                "lot_id": car["lot_id"],
                "lot_name": lot["name"],
                "space_id": car["space_id"],
                "parked_minutes": minutes,
                "entry_time": car["entry_time"],
                "total_fee": 0,
                "paid": True,
                "grace_period_min": 15,
                "fee_rule": rule
            }
            
        # 요금 계산
        if minutes <= rule["free_min"]:
            calc_fee = 0
        elif minutes <= rule["base_min"]:
            calc_fee = rule["base_fee"]
        else:
            add_min = minutes - rule["base_min"]
            unit_cnt = (add_min + rule["unit_min"] - 1) // rule["unit_min"]
            calc_fee = rule["base_fee"] + (unit_cnt * rule["unit_fee"])
            
        final_fee = min(calc_fee, rule["daily_max"])
        
        # 전기차/장애인 50% 할인 (공영/스마트 주차장 표준 혜택)
        discount = 0
        if car["type"] in ("ev", "accessible"):
            discount = final_fee // 2
            final_fee -= discount
            
        return {
            "car_id": car["car_id"],
            "plate_number": car["plate_number"],
            "lot_id": car["lot_id"],
            "lot_name": lot["name"],
            "space_id": car["space_id"],
            "parked_minutes": minutes,
            "entry_time": car["entry_time"],
            "total_fee": final_fee,
            "original_fee": final_fee + discount,
            "discount": discount,
            "discount_reason": "전기차/장애인 50% 감면" if discount > 0 else None,
            "paid": False,
            "fee_rule": rule
        }

    def pay_fee(self, car_id: str, amount: int, confirmation_token: str) -> Dict[str, Any]:
        # 경보 상태 체크 (Lv2 경보 중 결제 차단)
        for alert in self.active_alerts:
            if alert.get("level", 0) >= 2:
                return {
                    "success": False,
                    "error_code": "EVACUATION_ACTIVE",
                    "message": "현재 화재/비상 대피 경보가 발령되어 안전을 위해 결제가 차단되었습니다. 즉시 안전한 곳으로 대피하세요!"
                }
                
        car = self.find_car(car_id)
        if not car:
            return {"success": False, "message": "차량 정보를 찾을 수 없습니다."}
            
        self.paid_records.add(car["car_id"])
        receipt = {
            "success": True,
            "payment_id": f"PAY-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}",
            "car_id": car["car_id"],
            "plate_number": car["plate_number"],
            "lot_id": car["lot_id"],
            "lot_name": LOTS_CONFIG[car["lot_id"]]["name"],
            "amount": amount,
            "payment_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payment_method": "스마트 결제 (모의)",
            "exit_deadline": (datetime.datetime.now() + datetime.timedelta(minutes=15)).strftime("%H:%M")
        }
        return receipt

    def get_alerts(self, lot_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.active_alerts:
            return []
        if lot_id:
            return [a for a in self.active_alerts if a.get("lot_id") == lot_id]
        return self.active_alerts

    def set_mock_alert(self, lot_id: str, space_id: str, level: int = 2, message: str = "A구역 화재 감지"):
        alert = {
            "alert_id": f"ALT-{int(time.time())}",
            "lot_id": lot_id,
            "lot_name": LOTS_CONFIG.get(lot_id, {}).get("name", lot_id),
            "space_id": space_id,
            "level": level,
            "message": message,
            "detected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active"
        }
        self.active_alerts.append(alert)
        return alert

    def clear_alerts(self):
        self.active_alerts.clear()

    def call_admin(self, lot_id: str, space_id: Optional[str], reason: str) -> Dict[str, Any]:
        call_id = f"ADM-{datetime.datetime.now().strftime('%H%M%S')}-{len(self.admin_calls)+1}"
        record = {
            "call_id": call_id,
            "lot_id": lot_id,
            "lot_name": LOTS_CONFIG.get(lot_id, {}).get("name", "현장"),
            "space_id": space_id or "전체",
            "reason": reason or "고객 현장 지원 요청",
            "created_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "status": "대기중 (현장 관리자 배정 중)"
        }
        self.admin_calls.append(record)
        return record

    def get_ev_charging_status(self, lot_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """전기차 충전 현황: 충전율(%), 남은 충전 시간(분), 충전기 타입, 상태"""
        now = datetime.datetime.now()
        chargers = [
            # 개발원 주차장 (2면: A-05, A-06)
            {
                "charger_id": "EV-A05",
                "lot_id": "A",
                "lot_name": "개발원 주차장",
                "space_id": "A-05",
                "charger_type": "급속 100kW",
                "status": "charging",
                "status_text": "충전 중",
                "car_id": "23",
                "plate_number": "12가 3423",
                "battery_percent": 78,
                "remaining_minutes": 14,
                "current_speed_kw": 84.5,
                "start_time": (now - datetime.timedelta(minutes=32)).strftime("%H:%M")
            },
            {
                "charger_id": "EV-A06",
                "lot_id": "A",
                "lot_name": "개발원 주차장",
                "space_id": "A-06",
                "charger_type": "완속 7kW",
                "status": "available",
                "status_text": "충전 가능 (빈자리)",
                "car_id": None,
                "plate_number": None,
                "battery_percent": 0,
                "remaining_minutes": 0,
                "current_speed_kw": 0,
                "start_time": None
            },
            # 부산역 주차장 (2면: A-07, A-08)
            {
                "charger_id": "EV-B07",
                "lot_id": "B",
                "lot_name": "부산역 주차장",
                "space_id": "A-07",
                "charger_type": "급속 100kW",
                "status": "charging",
                "status_text": "충전 완료 임박",
                "car_id": "58",
                "plate_number": "78라 9058",
                "battery_percent": 92,
                "remaining_minutes": 6,
                "current_speed_kw": 25.0,
                "start_time": (now - datetime.timedelta(minutes=42)).strftime("%H:%M")
            },
            {
                "charger_id": "EV-B08",
                "lot_id": "B",
                "lot_name": "부산역 주차장",
                "space_id": "A-08",
                "charger_type": "완속 7kW",
                "status": "available",
                "status_text": "충전 가능 (빈자리)",
                "car_id": None,
                "plate_number": None,
                "battery_percent": 0,
                "remaining_minutes": 0,
                "current_speed_kw": 0,
                "start_time": None
            },
            # 김해공항 주차장 (6면: A-11, A-12, B-11, B-12, B-31, B-32)
            {
                "charger_id": "EV-C11",
                "lot_id": "C",
                "lot_name": "김해공항 (P1)",
                "space_id": "A-11",
                "charger_type": "급속 100kW",
                "status": "charging",
                "status_text": "충전 중",
                "car_id": "57",
                "plate_number": "89마 5757",
                "battery_percent": 45,
                "remaining_minutes": 28,
                "current_speed_kw": 92.0,
                "start_time": (now - datetime.timedelta(minutes=20)).strftime("%H:%M")
            },
            {
                "charger_id": "EV-C12",
                "lot_id": "C",
                "lot_name": "김해공항 (P1)",
                "space_id": "A-12",
                "charger_type": "급속 100kW",
                "status": "available",
                "status_text": "충전 가능 (빈자리)",
                "car_id": None,
                "plate_number": None,
                "battery_percent": 0,
                "remaining_minutes": 0,
                "current_speed_kw": 0,
                "start_time": None
            },
            {
                "charger_id": "EV-C21",
                "lot_id": "C",
                "lot_name": "김해공항 (P2)",
                "space_id": "B-11",
                "charger_type": "급속 100kW",
                "status": "charging",
                "status_text": "충전 중",
                "car_id": "85",
                "plate_number": "23노 8585",
                "battery_percent": 63,
                "remaining_minutes": 19,
                "current_speed_kw": 75.3,
                "start_time": (now - datetime.timedelta(minutes=25)).strftime("%H:%M")
            },
            {
                "charger_id": "EV-C31",
                "lot_id": "C",
                "lot_name": "김해공항 (P3)",
                "space_id": "B-31",
                "charger_type": "완속 7kW",
                "status": "completed",
                "status_text": "완충 완료 (출차 대기)",
                "car_id": "42",
                "plate_number": "42오 4242",
                "battery_percent": 100,
                "remaining_minutes": 0,
                "current_speed_kw": 0,
                "start_time": (now - datetime.timedelta(minutes=150)).strftime("%H:%M")
            },
            # 신세계백화점 주차장 (8면: C-05~08, D-05~08)
            {
                "charger_id": "EV-D05",
                "lot_id": "D",
                "lot_name": "신세계백화점",
                "space_id": "C-05",
                "charger_type": "완속 7kW",
                "status": "charging",
                "status_text": "충전 중",
                "car_id": "11",
                "plate_number": "11구 1111",
                "battery_percent": 70,
                "remaining_minutes": 45,
                "current_speed_kw": 6.8,
                "start_time": (now - datetime.timedelta(minutes=65)).strftime("%H:%M")
            },
            {
                "charger_id": "EV-D06",
                "lot_id": "D",
                "lot_name": "신세계백화점",
                "space_id": "C-06",
                "charger_type": "완속 7kW",
                "status": "available",
                "status_text": "충전 가능 (빈자리)",
                "car_id": None,
                "plate_number": None,
                "battery_percent": 0,
                "remaining_minutes": 0,
                "current_speed_kw": 0,
                "start_time": None
            },
            {
                "charger_id": "EV-D07",
                "lot_id": "D",
                "lot_name": "신세계백화점",
                "space_id": "D-05",
                "charger_type": "급속 100kW",
                "status": "charging",
                "status_text": "충전 중",
                "car_id": "99",
                "plate_number": "99하 9999",
                "battery_percent": 55,
                "remaining_minutes": 22,
                "current_speed_kw": 88.0,
                "start_time": (now - datetime.timedelta(minutes=18)).strftime("%H:%M")
            },
            {
                "charger_id": "EV-D08",
                "lot_id": "D",
                "lot_name": "신세계백화점",
                "space_id": "D-06",
                "charger_type": "급속 100kW",
                "status": "available",
                "status_text": "충전 가능 (빈자리)",
                "car_id": None,
                "plate_number": None,
                "battery_percent": 0,
                "remaining_minutes": 0,
                "current_speed_kw": 0,
                "start_time": None
            }
        ]

        if lot_id:
            return [c for c in chargers if c["lot_id"] == lot_id]
        return chargers

