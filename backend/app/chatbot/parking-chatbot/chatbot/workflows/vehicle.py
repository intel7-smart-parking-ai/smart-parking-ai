"""Lookup and selection of recognized vehicles and anonymous candidates."""
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import time
import uuid

from .. import config, templates as T
from ..adapters import ApiAdapter, CachedAdapter, DataUnavailable
from ..contracts import LOCATION_PENDING, timestamp
from .views import stale_note


@dataclass
class VehicleLookup:
    adapter: CachedAdapter
    current_alert: Callable
    find_vehicle: Callable
    record_evidence: Callable
    respond: Callable

    def anonymous(self, s, lot_id=None):
        """Offer at most three anonymous candidates, outside an active Lv2 alert."""
        if self.current_alert().get("level",0)>=2:
            return "경보 중 차량 위치 확인은 관리자에게 요청해 주세요. 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요.", None, "alert", None
        s.candidate_options.clear()
        api = isinstance(self.adapter.inner, ApiAdapter)
        try:
            rows = self.adapter.list_cars(lot_id, candidates=True) if api else self.adapter.list_cars()
            cars = [c for c in rows if c.get("car_id") is None]
            if api and not cars:
                raise DataUnavailable("번호 미인식 차량 목록 없음")
        except DataUnavailable:
            return T.CAR_NOT_FOUND.format(no=s.car_id) + ("" if api else " 번호 미인식 차량 목록도 확인하지 못했어요."), None, (["unrecognized", "call_admin"] if api else ["call_admin"]), None
        if api and any(car.get("is_mock") for car in cars):
            self.record_evidence({"is_mock": True})
        cars.sort(key=lambda c: timestamp(c['entry_at']), reverse=True)
        if not cars: return T.CAR_NOT_FOUND.format(no=s.car_id), None, ["call_admin","change_vehicle"], None
        s.candidate_options = {uuid.uuid4().hex:c for c in cars[:3]}
        s.candidate_expires = time.monotonic()+120
        if len(cars)>3:
            return f"{s.car_id}번 차의 입차 기록이 없고, 번호 미인식 차량이 {len(cars)}대 있어요. 관리자에게 확인해 주세요.", None, ["candidate_admin"], None
        first=cars[0]
        entered = timestamp(first["entry_at"]).astimezone(config.KST).isoformat() if api else first["entry_at"]
        text=f"{s.car_id}번 차의 입차 기록이 없어요.\n번호 미인식 차량이 {T.place(first['lot_id'], first['spot_no'], first.get('space_id'))}에 있어요 ({T.hhmm(entered)} 입차). 혹시 이 차인가요?"
        return text, {"type":"unrecognized_candidates","data":{"cars":cars,"notice":"후보 위치만 안내해요. 요금·결제는 관리자 확인 후 가능해요."}}, ["candidate:"+token for token in s.candidate_options]+["candidate_admin"], None

    def location(self, s, p):
        d = self.find_vehicle(s.car_id)
        self.record_evidence(d)
        if not d:
            return self.anonymous(s, p.get("lot_id"))
        if d.get("status") == LOCATION_PENDING:
            return T.car_pending(d), None, ["find_car", "call_admin"], d.get("updated_at")
        s.selected_candidate=None; s.candidate_options.clear()
        card = {"type": "car_location", "data": d}
        return T.car_location(d) + stale_note(d), card, "after_find_my_car", d["updated_at"]

    def select_candidate(self, s, token):
        if self.current_alert().get("level",0)>=2:
            s.clear()
            return self.respond(s,"경보 중 후보 위치 확인은 관리자에게 요청해 주세요. 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요.",chip_set="alert")
        car=s.candidate_options.get(token)
        s.clear()
        if not car or time.monotonic()>=s.candidate_expires:
            return self.respond(s,"이전 후보 안내가 만료됐어요. 내 차 위치를 다시 조회해 주세요.",chip_set=["find_car","call_admin"])
        api = isinstance(self.adapter.inner, ApiAdapter)
        try:
            current = self.adapter.list_cars(car["lot_id"], candidates=True) if api else self.adapter.list_cars(car["lot_id"])
        except DataUnavailable:
            return self.respond(s,"후보 차량의 현재 위치를 확인하지 못했어요. 관리자에게 확인해 주세요.",chip_set=["candidate_admin"])
        space_key = "space_id" if api else "spot_no"
        if not any(c.get("car_id") is None and c[space_key] == car[space_key]
                   and c["entry_at"] == car["entry_at"] for c in current):
            s.candidate_options.clear()
            return self.respond(s,"후보 차량 정보가 바뀌었어요. 다시 조회해 주세요.",chip_set=["find_car","call_admin"])
        s.selected_candidate=deepcopy(car)
        if api:
            self.record_evidence(car)
        return self.respond(s,"번호 미인식 차량의 후보 위치예요. 본인 차량인지 현장에서 확인해 주세요.\n요금·결제는 관리자 확인 후 가능해요.",
            {"type":"unrecognized_candidates","data":{"cars":[car],"notice":"차량 번호와 연결되지 않은 위치 후보입니다."}},["candidate_admin","change_vehicle"])
