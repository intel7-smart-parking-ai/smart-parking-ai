"""외부 데이터가 엔진에 들어오기 전 최소 계약 검증. 잘못된 값을 정상 상태로 추정하지 않는다."""
from datetime import datetime
import math
from . import config
from .catalog import SAFE_ID


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def timestamp(value):
    require(isinstance(value, str), "시각은 ISO 문자열이어야 합니다.")
    dt = datetime.fromisoformat(value)
    require(dt.tzinfo is not None, "시각에 시간대가 필요합니다.")
    return dt


def validate(kind, value):
    if kind in ("car", "fee") and value is None:
        return value
    if kind == "cars":
        require(isinstance(value, list), "주차 차량 목록은 배열이어야 합니다.")
        for car in value:
            validate("car_ref", car)
        return value
    require(isinstance(value, dict), f"{kind} 응답은 객체여야 합니다.")
    if kind == "empty":
        require(isinstance(value.get("lots"), dict) and bool(value["lots"]), "lots가 필요합니다.")
        for lot, item in value["lots"].items():
            require(isinstance(lot, str) and isinstance(item, dict), "주차장 정보 형식 오류")
            require(bool(SAFE_ID.fullmatch(lot)), "주차장 ID 형식 오류")
            require(integer(item.get("total"), 1) and integer(item.get("empty")) and
                    integer(item.get("uncertain",0)) and item["empty"] + item.get("uncertain",0) <= item["total"], "빈자리 수가 용량 범위를 벗어났습니다.")
            # PROVISIONAL(API v1 확정 필요): zones, 운영 상태, 명부 메타데이터.
            if "entry_allowed" in item:require(type(item["entry_allowed"]) is bool,"entry_allowed는 boolean이어야 합니다.")
            if "restriction_reason" in item:require(isinstance(item["restriction_reason"],str),"제한 사유 형식 오류")
            if "kind" in item:require(item["kind"] in ("real","virtual"),"실물/가상 구분 오류")
            if "name" in item:require(isinstance(item["name"],str) and bool(item["name"]),"주차장 이름 오류")
            if "zones" in item:
                require(isinstance(item["zones"],dict),"zones는 객체여야 합니다.")
                for key, zone in item["zones"].items():
                    require(isinstance(key,str) and bool(SAFE_ID.fullmatch(key)) and isinstance(zone,dict),"구역 형식 오류")
                    require(integer(zone.get("total")) and integer(zone.get("empty")) and integer(zone.get("uncertain",0)) and zone["empty"]+zone.get("uncertain",0)<=zone["total"],"구역 자리 수 오류")
                require(sum(z["total"] for z in item["zones"].values())<=item["total"] and
                        sum(z["empty"] for z in item["zones"].values())<=item["empty"] and
                        sum(z.get("uncertain",0) for z in item["zones"].values())<=item.get("uncertain",0),"구역 집계가 전체 자리 수를 초과했습니다.")
    elif kind in ("car", "car_ref"):
        require((kind == "car_ref" and value.get("vehicle_no") is None) or (isinstance(value.get("vehicle_no"), str) and bool(value["vehicle_no"])), "vehicle_no 필요")
        if value.get("vehicle_no") is None: timestamp(value.get("entered_at"))
        require(isinstance(value.get("lot_id"), str) and bool(value["lot_id"]), "lot_id 필요")
        require(value["lot_id"] in config.catalog.ids(), "등록되지 않은 주차장입니다.")
        require(integer(value.get("spot_no"), 1) and value["spot_no"] <= config.catalog.lot(value["lot_id"])["capacity"], "spot_no 범위 오류")
        if kind == "car":
            timestamp(value.get("entered_at"))
            require(integer(value.get("capacity"), value["spot_no"]), "capacity 형식 오류")
    elif kind == "fee":
        require(isinstance(value.get("vehicle_no"), str), "vehicle_no 필요")
        timestamp(value.get("entered_at"))
        for key in ("minutes", "fee", "fee_after_30", "free_minutes"):
            require(integer(value.get(key)), f"{key}는 0 이상 정수여야 합니다.")
        require(type(value.get("paid")) is bool, "paid는 boolean이어야 합니다.")
    elif kind == "alert":
        require(type(value.get("level")) is int and value["level"] in (0, 1, 2), "경보 단계 형식 오류")
        require(integer(value.get("version")), "경보 버전 형식 오류")
        if value["level"]:
            require(isinstance(value.get("lot_id"), str), "경보 lot_id 필요")
            require(value["lot_id"] in config.catalog.ids(), "등록되지 않은 경보 주차장입니다.")
            require(integer(value.get("spot_no"), 1) and value["spot_no"] <= config.catalog.lot(value["lot_id"])["capacity"], "경보 spot_no 범위 오류")
            if "started_at" in value:
                timestamp(value["started_at"])
        if "updated_at" in value:
            timestamp(value["updated_at"])
        if value["level"]:
            confidence = value.get("confidence", 0)
            require(type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1,
                    "경보 confidence 형식 오류")
    elif kind == "payment":
        require(value.get("status") in {"paid", "free", "already_paid", "not_found", "quote_changed"},
                "알 수 없는 결제 결과입니다.")
        if value["status"] in ("paid", "free"):
            require(integer(value.get("amount")), "결제 금액 형식 오류")
        if value["status"] in ("paid", "free", "already_paid"):
            timestamp(value.get("paid_at"))
    if kind in ("empty", "car", "fee"):
        timestamp(value.get("updated_at"))
    return value
