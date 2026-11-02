"""설정과 백엔드의 주차장 명부. PROVISIONAL(API v1 확정 필요)."""
from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path
import re
import threading

SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")

class Catalog:
    def __init__(self, settings):
        self.lock = threading.RLock()
        self.configure(settings)

    def configure(self, settings):
        doc = deepcopy(settings)
        lots, zones = doc["lots"], doc["zones"]
        if not lots or len({x["lot_id"] for x in lots}) != len(lots):
            raise ValueError("주차장 목록과 고유 ID가 필요합니다.")
        for lot in lots:
            if not SAFE_ID.fullmatch(lot["lot_id"]) or type(lot["capacity"]) is not int or lot["capacity"] < 1 or lot["kind"] not in ("real","virtual"):
                raise ValueError("주차장 설정 형식 오류")
        if not zones or len({z["key"] for z in zones}) != len(zones) or sum(bool(z.get("remaining")) for z in zones) != 1:
            raise ValueError("구역 키와 잔여 칸 배정 구역 하나가 필요합니다.")
        if any(not SAFE_ID.fullmatch(z["key"]) or type(z.get("slots_per_lot",0)) is not int or z.get("slots_per_lot",0)<0 for z in zones):
            raise ValueError("구역 설정 형식 오류")
        if (type(doc["vehicle_min"]) is not int or type(doc["vehicle_max"]) is not int
                or not 1 <= doc["vehicle_min"] <= doc["vehicle_max"] <= 99):
            raise ValueError("차량 번호 범위 오류")
        ev = doc.get("ev_vehicles", [])
        if (not isinstance(ev, list) or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{2}", v)
                or not doc["vehicle_min"] <= int(v) <= doc["vehicle_max"] for v in ev) or len(set(ev)) != len(ev)):
            raise ValueError("전기차 번호 목록 형식 오류")
        zone_keys = {z["key"] for z in zones}
        for lot in lots:
            layout = lot.get("zone_layout")
            if layout is not None:
                if not isinstance(layout, dict) or any(k not in zone_keys or not isinstance(v, list) for k,v in layout.items()):
                    raise ValueError("구역 배치 형식 오류")
                spots = [n for values in layout.values() for n in values]
                if any(type(n) is not int or not 1 <= n <= lot["capacity"] for n in spots) or len(set(spots)) != len(spots):
                    raise ValueError("구역 배치 칸 범위 또는 중복 오류")
            exits = lot.get("exits", [])
            if not isinstance(exits, list) or any(not isinstance(x,str) or not x.strip() for x in exits) or len(set(exits)) != len(exits):
                raise ValueError("출구 목록 형식 오류")
            facility = lot.get("facility", {})
            if not isinstance(facility, dict): raise ValueError("설비 정보 형식 오류")
            for key in ("underground", "sprinkler"):
                if facility.get(key) is not None and type(facility[key]) is not bool:
                    raise ValueError("설비 상태는 boolean 또는 null이어야 합니다.")
            height = facility.get("height_limit_m")
            if height is not None and (type(height) not in (int,float) or not math.isfinite(height) or height <= 0):
                raise ValueError("높이 제한 형식 오류")
        with self.lock:
            self.settings = doc
            self.reset()

    def reset(self):
        with self.lock:
            self._lots = {x["lot_id"]: deepcopy(x) for x in self.settings["lots"]}
            self.source = "settings"

    def lots(self):
        with self.lock:return deepcopy(self._lots)

    def ids(self):
        with self.lock:return list(self._lots)

    def canonical_id(self, value):
        ids = self.ids()
        if not isinstance(value,str): return None
        if value in ids: return value
        matches = [key for key in ids if key.casefold() == value.casefold()]
        return matches[0] if len(matches)==1 else None

    def lot(self, key):
        with self.lock:return deepcopy(self._lots.get(key))

    def zones(self):
        with self.lock:return deepcopy(self.settings["zones"])

    def name(self, key):
        return (self.lot(key) or {}).get("name", str(key))

    def zone_name(self, key):
        return next((z["name"] for z in self.zones() if z["key"] == key), str(key))

    def zone_spots(self, capacity, lot_id=None):
        zones = self.zones()
        layout = (self.lot(lot_id) or {}).get("zone_layout")
        if layout is not None:
            if any(n > capacity for values in layout.values() for n in values):
                raise ValueError("명부 용량과 구역 배치가 다릅니다.")
            assigned = {n for values in layout.values() for n in values}
            return {z["key"]: list(layout.get(z["key"], [])) if not z.get("remaining") or z["key"] in layout
                    else sorted(set(range(1,capacity+1))-assigned) for z in zones}
        reserved = sum(z.get("slots_per_lot",0) for z in zones if not z.get("remaining"))
        if reserved > capacity:raise ValueError("칸 수보다 구역 배정 수가 많습니다.")
        result, offset = {}, 1
        for z in zones:
            count = capacity-reserved if z.get("remaining") else z.get("slots_per_lot",0)
            result[z["key"]] = list(range(offset,offset+count));offset += count
        return result

    def adopt(self, lots):
        """검증·신선도 확인이 끝난 API 응답에만 호출한다."""
        with self.lock:
            fallback = {x["lot_id"]:x for x in self.settings["lots"]}
            incoming = {key:{**fallback.get(key,{}),"lot_id":key,"name":v.get("name",fallback.get(key,{}).get("name",key)),
                        "kind":v.get("kind",fallback.get(key,{}).get("kind","unknown")),"capacity":v["total"]} for key,v in lots.items()}
            for key, lot in incoming.items():
                if lot.get("zone_layout") and any(n > lot["capacity"] for v in lot["zone_layout"].values() for n in v):
                    lot.pop("zone_layout")
            if incoming != self._lots:
                logging.getLogger(__name__).warning("주차장 명부를 백엔드 기준으로 갱신: %s",list(incoming))
            self._lots, self.source = incoming, "backend"

    def public(self):
        return {"lots":[{k:v for k,v in x.items() if k in ("lot_id","name","kind","capacity")} for x in self.lots().values()],
                "zones":[{k:z[k] for k in ("key","name")} for z in self.zones()],"source":self.source}

    def chips(self):
        result = {}
        for lot in self.lots().values():
            key, name = lot["lot_id"], lot["name"]
            result["lot_"+key] = {"label":name+"만 보기","intent":"get_empty_spots","params":{"lot_id":key}}
            for zone in [None]+[z["key"] for z in self.zones()]:
                suffix = ":"+zone if zone else ""
                params = {"lot_id":key,**({"zone":zone} if zone else {})}
                result["go:"+key+suffix] = {"label":name+"으로 안내","intent":"set_destination","params":params}
        for zone in self.zones():
            result["zone:"+zone["key"]] = {"label":zone["name"]+" 구역 빈자리","intent":"get_empty_spots","params":{"zone":zone["key"]}}
        return result


def load_catalog():
    path = Path(os.getenv("PARKING_SETTINGS_PATH",Path(__file__).with_name("lots.json")))
    return Catalog(json.loads(path.read_text(encoding="utf-8")))
