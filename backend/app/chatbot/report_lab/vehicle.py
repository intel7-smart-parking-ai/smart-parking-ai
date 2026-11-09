"""Resolve one vehicle from the existing read APIs, preserving ambiguity."""
import re
from urllib.error import HTTPError
from urllib.parse import quote

from .query import Clarification, core, name
from .source import read


def _valid(data, query):
    if not isinstance(data, dict) or data.get("lot_id") not in ("A", "B", "C", "D"):
        raise ValueError("Invalid vehicle location")
    if not re.fullmatch(r"[A-Z]-\d{2}", str(data.get("space_id", ""))) or data.get("status") != "occupied":
        raise ValueError("Unconfirmed vehicle location")
    if query.vehicle_kind == "sticker":
        matches = data.get("car_id") == query.vehicle_id
    else:
        matches = str(data.get("vehicle_number") or data.get("plate_number") or "")[-4:] == query.vehicle_id
        if query.full_plate:
            matches = matches and data.get("plate_number") == query.full_plate
    if not matches or (query.lots and data["lot_id"] not in query.lots):
        raise Clarification("입력한 번호와 주차장 조건으로 차량 한 대를 확인하지 못했어요. 번호와 주차장을 다시 알려주세요.")
    core()[3].timestamp(data["observed_at"])
    return data


def fetch(_day, query):
    if query.vehicle_problem or not query.vehicle_id:
        raise Clarification("스티커 두 자리 번호 또는 차량번호 뒤 네 자리를 하나만 알려주세요.")
    number = query.vehicle_id
    if query.vehicle_kind == "plate":
        sample = {"vehicle_number": "4268", "lot_id": "D", "space_id": "C-09",
                  "session_id": "demo-session-4268", "status": "occupied",
                  "observed_at": "2026-11-08T09:00:00+09:00"}
        try:
            data = read("/api/v1/vehicle-search?vehicle_number=" + quote(number, safe=""), sample)
        except HTTPError as exc:
            if exc.code != 404:
                raise
            lots = query.lots or ["C", "D"]
            matches = []
            for lot in lots:
                rows = read(f"/api/v1/parking-lots/{lot}/spaces", {"items": []})["items"]
                if not isinstance(rows, list):
                    raise ValueError("Invalid vehicle candidates")
                matches.extend({**row, "lot_id": lot} for row in rows if row.get("status") == "occupied"
                               and str(row.get("vehicle_number") or row.get("plate_number") or "")[-4:] == number
                               and (not query.full_plate or row.get("plate_number") == query.full_plate))
            if len(matches) != 1:
                raise Clarification("해당 뒤 네 자리로 단일 차량을 확인하지 못했어요. 주차장을 지정해 다시 조회하거나 관리자에게 확인해 주세요.")
            data = matches[0]
    else:
        sample = {"car_id": "23", "lot_id": "A", "space_id": "A-01",
                  "session_id": "demo-session-23", "status": "occupied",
                  "observed_at": "2026-11-08T09:00:00+09:00"}
        try:
            data = read("/api/v1/vehicles/" + quote(number, safe="") + "/location", sample)
        except HTTPError as exc:
            if exc.code != 404:
                raise
            raise Clarification("현재 API에서 해당 번호의 단일 차량을 확인하지 못했어요. 번호·주차장과 입차 인식 상태를 확인해 주세요.") from None
    data = _valid(data, query)
    query.resolved_vehicle = data
    return {"차량 번호": data.get("plate_number") or number, "주차장": name(data["lot_id"]),
            "주차칸": data["space_id"], "조회 기준 시각": data["observed_at"]}
