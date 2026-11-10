"""설정과 백엔드의 주차장 명부. PROVISIONAL(API v1 확정 필요)."""
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import threading

from .diagnostics import logger
from .snapshots import freeze

SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")

SPACE_ID = re.compile(r"[A-Z]-[0-9]{2}\Z")
SPACE_INPUT = re.compile(r"([A-Za-z])\s*-?\s*([0-9]{1,2})\Z")
RATE_KEYS = ("base_minutes", "base_fee", "unit_minutes", "extra_fee", "daily_cap")
# info_gaps.TOPICS의 키와 같아야 한다(test_v2160_natural_replies에서 확인).
GUIDE_KEYS = frozenset({"terminal", "elevator", "stairs", "restroom", "entrance", "hours", "shuttle", "height", "amenity",
                        "discount", "reservation", "overnight", "refund", "payment_help", "exit_rules", "lookup_help", "discount_steps", "walking", "ev_usage", "lost_found", "vehicle_rules", "passes"})


def normalize_space(value):
    """'B6'·'b-6'·'B-06' → 'B-06'. 형식이 아니면 None."""
    if not isinstance(value, str):
        return None
    match = SPACE_INPUT.fullmatch(value.strip())
    if not match or not 1 <= int(match[2]) <= 99:
        return None
    return f"{match[1].upper()}-{int(match[2]):02d}"


class Catalog:
    """주차장 명부. 내부 칸 식별은 주차장별 칸 목록의 순번(1부터), 외부 표기는 도면 번호(space_id)."""

    def __init__(self, settings):
        self.lock = threading.RLock()
        self.version = 0
        self.configure(settings)

    def configure(self, settings):
        doc = deepcopy(settings)
        lots, zones = doc["lots"], doc["zones"]
        if not lots or len({x["lot_id"] for x in lots}) != len(lots):
            raise ValueError("주차장 목록과 고유 ID가 필요합니다.")
        for lot in lots:
            where = f"{lot.get('lot_id')} 주차장"
            if not SAFE_ID.fullmatch(lot["lot_id"]) or lot["kind"] not in ("real","virtual"):
                raise ValueError("주차장 설정 형식 오류")
            spaces = lot.get("spaces")
            if (not isinstance(spaces, list) or not spaces or len(set(spaces)) != len(spaces)
                    or any(not isinstance(x, str) or not SPACE_ID.fullmatch(x) for x in spaces)):
                raise ValueError(f"{where} spaces는 'A-01' 형식의 중복 없는 칸 이름 목록이어야 합니다.")
            # 칸 수는 spaces 목록에서만 정한다(capacity 값은 무시하고 다시 계산).
            lot["capacity"] = len(spaces)
            if "order_alias" in lot and (not isinstance(lot["order_alias"], str) or len(lot["order_alias"]) != 1):
                raise ValueError(f"{where} order_alias는 한 글자(가·나·다·라)여야 합니다.")
            for key in ("aliases", "loose_aliases"):
                if not isinstance(lot.get(key, []), list) or any(not isinstance(x, str) or not x.strip() for x in lot.get(key, [])):
                    raise ValueError(f"{where} {key} 형식 오류")
            policy = lot.get("rate_policy")
            if policy is not None and (not isinstance(policy, dict) or set(policy) != set(RATE_KEYS)
                                       or any(type(policy[k]) is not int or policy[k] <= 0 for k in RATE_KEYS)):
                raise ValueError(f"{where} rate_policy는 {', '.join(RATE_KEYS)} 양의 정수여야 합니다.")
        if not zones or len({z["key"] for z in zones}) != len(zones) or sum(bool(z.get("remaining")) for z in zones) != 1:
            raise ValueError("구역 키와 잔여 칸 배정 구역 하나가 필요합니다.")
        if any(not SAFE_ID.fullmatch(z["key"]) for z in zones):
            raise ValueError("구역 설정 형식 오류")
        if (type(doc["vehicle_min"]) is not int or type(doc["vehicle_max"]) is not int
                or not 10 <= doc["vehicle_min"] <= doc["vehicle_max"] <= 99):
            raise ValueError("차량 번호 범위 오류: 두 자리(10~99) 안이어야 합니다.")
        def valid_car(v):
            return isinstance(v, str) and re.fullmatch(r"[1-9][0-9]", v) and doc["vehicle_min"] <= int(v) <= doc["vehicle_max"]
        ev = doc.get("ev_vehicles", [])
        if not isinstance(ev, list) or any(not valid_car(v) for v in ev) or len(set(ev)) != len(ev):
            raise ValueError("전기차 번호 목록 형식 오류")
        types = doc.get("mock_vehicle_types", {})
        if (not isinstance(types, dict) or any(not valid_car(v) or kind not in
                ("general", "ev", "accessible") for v, kind in types.items())):
            raise ValueError("목업 차량 종류 목록 형식 오류")
        if types and {v for v, kind in types.items() if kind == "ev"} != set(ev):
            raise ValueError("목업 전기차 종류와 전기차 번호 목록이 다릅니다.")
        assigned = doc.get("assigned_car_ids")
        if assigned is not None and (not isinstance(assigned, list) or any(not valid_car(v) for v in assigned)
                                     or len(set(assigned)) != len(assigned)):
            raise ValueError("assigned_car_ids는 null 또는 두 자리 번호 문자열 목록이어야 합니다.")
        zone_keys = {z["key"] for z in zones}
        remaining_key = next(z["key"] for z in zones if z.get("remaining"))
        for lot in lots:
            layout = lot.get("zone_layout")
            if layout is not None:
                if not isinstance(layout, dict) or any(k not in zone_keys or k == remaining_key or not isinstance(v, list) for k,v in layout.items()):
                    raise ValueError("구역 배치 형식 오류")
                labels = [x for values in layout.values() for x in values]
                if any(x not in lot["spaces"] for x in labels) or len(set(labels)) != len(labels):
                    raise ValueError(f"{lot['lot_id']} 주차장 구역 배치에 없는 칸 또는 중복 칸이 있습니다.")
            exits = lot.get("exits", [])
            if not isinstance(exits, list) or any(not isinstance(x,str) or not x.strip() for x in exits) or len(set(exits)) != len(exits):
                raise ValueError("출구 목록 형식 오류")
            # 설비 값 규칙 (docs/settings_guide.md): null=미확인, true=있음, false=없음.
            # 높이 제한은 null=미확인, false=해당 없음, 양수=미터.
            facility = lot.get("facility", {})
            where = f"{lot['lot_id']} 주차장 facility"
            if not isinstance(facility, dict): raise ValueError(f"{where} 형식 오류: 객체여야 합니다.")
            unknown = set(facility) - {"underground", "sprinkler", "height_limit_m"}
            if unknown:
                raise ValueError(f"{where}에 알 수 없는 항목 {sorted(unknown)}. 사용 가능: underground, sprinkler, height_limit_m")
            for key in ("underground", "sprinkler"):
                if facility.get(key) is not None and type(facility[key]) is not bool:
                    raise ValueError(f"{where}.{key}는 true/false/null 중 하나여야 합니다.")
            height = facility.get("height_limit_m")
            if height is not None and height is not False and (type(height) not in (int,float) or not math.isfinite(height) or height <= 0):
                raise ValueError(f"{where}.height_limit_m은 null(미확인), false(해당 없음), 양수(미터) 중 하나여야 합니다.")
            # 시설 안내 문장(docs/v2160_natural_replies.md). 팀이 확인한 문장만 넣고, 비어 있으면 '미등록'으로 답한다.
            guide = lot.get("guide", {})
            where = f"{lot['lot_id']} 주차장 guide"
            if not isinstance(guide, dict): raise ValueError(f"{where} 형식 오류: 객체여야 합니다.")
            unknown = set(guide) - GUIDE_KEYS
            if unknown:
                raise ValueError(f"{where}에 알 수 없는 항목 {sorted(unknown)}. 사용 가능: {', '.join(sorted(GUIDE_KEYS))}")
            if any(not isinstance(v, str) for v in guide.values()):
                raise ValueError(f"{where} 값은 문장(문자열)이어야 합니다.")
        with self.lock:
            self.settings = doc
            self.reset()

    @property
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = freeze(value)

    def reset(self):
        with self.lock:
            self._configured = {row["lot_id"]: row for row in self.settings["lots"]}
            self._zone_patterns = tuple(
                (z["key"], re.compile(
                    r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])" if alias.isascii() else re.escape(alias), re.I
                ))
                for z in self.settings["zones"] for alias in z.get("aliases", [])
            )
            self._publish(self._configured, "settings")

    @staticmethod
    def _lot_patterns(lots):
        patterns = []
        for key, row in lots.items():
            aliases = [re.compile(r"(?<![a-z0-9_])" + re.escape(key) + r"(?![a-z0-9_\-])", re.I)]
            aliases.extend(re.compile(r"(?<![가-힣])" + re.escape(alias) + r"\s*(?:주차장|구역|동)")
                           for alias in row.get("aliases", []))
            if row.get("order_alias"):
                aliases.append(re.compile(r"(?<![가-힣])" + re.escape(row["order_alias"]) + r"\s*주차장"))
            contextual = tuple(re.compile(r"(?<![가-힣])" + re.escape(alias))
                               for alias in row.get("loose_aliases", []))
            name = row["name"]
            patterns.append((key, tuple(aliases), contextual, (name, name.replace("주차장", " 주차장"))))
        return tuple(patterns)

    def _publish(self, lots, source):
        """Publish a coherent immutable view while the writer owns the catalog lock."""
        self._lots = freeze(lots)
        self.source = source
        self._spot_indices = {
            key: {label: index for index, label in enumerate(row.get("spaces", []), 1)}
            for key, row in {**self._configured, **lots}.items()
        }
        self._active_patterns = self._lot_patterns(lots)
        self._api_patterns = self._lot_patterns({**self._configured, **lots})
        self._public = freeze({
            "lots": [{k: v for k, v in lot.items() if k in ("lot_id", "name", "kind", "capacity", "spaces")}
                     for lot in lots.values()],
            "zones": [{k: z[k] for k in ("key", "name")} for z in self.settings["zones"]],
            "source": source,
        })
        self._chips = freeze(self._build_chips())
        self.version += 1

    def lot_patterns(self, api=False):
        with self.lock:
            return self._api_patterns if api else self._active_patterns

    def zone_patterns(self):
        with self.lock:
            return self._zone_patterns

    def _settings_lot(self, key):
        return self._configured.get(key)

    def lots(self):
        with self.lock:
            return self._lots

    def ids(self):
        with self.lock:return list(self._lots)

    def canonical_id(self, value):
        ids = self.ids()
        if not isinstance(value,str): return None
        if value in ids: return value
        matches = [key for key in ids if key.casefold() == value.casefold()]
        return matches[0] if len(matches)==1 else None

    def lot(self, key):
        with self.lock:
            return self._lots.get(key)

    def zones(self):
        with self.lock:
            return self.settings["zones"]

    def name(self, key):
        with self.lock:
            lot = self._lots.get(key) or self._settings_lot(key)
            return lot.get("name", str(key)) if lot else str(key)

    def zone_name(self, key):
        return next((z["name"] for z in self.settings["zones"] if z["key"] == key), str(key))

    # ------------------------------------------------------------ 칸 이름(도면 번호)
    def spaces(self, lot_id):
        with self.lock:
            lot = self._lots.get(lot_id) or self._settings_lot(lot_id) or {}
            return list(lot.get("spaces", []))

    def space_count(self, lot_id):
        with self.lock:
            lot = self._lots.get(lot_id) or {}
            return max(lot.get("capacity", 0), len(lot.get("spaces", [])))

    def space_label(self, lot_id, spot):
        """내부 순번 → 도면 번호. 모르면 None."""
        if spot is None:
            return None
        with self.lock:
            lot = self._lots.get(lot_id) or self._settings_lot(lot_id) or {}
            labels = lot.get("spaces", ())
            return labels[spot - 1] if type(spot) is int and 1 <= spot <= len(labels) else None

    def space_index(self, lot_id, label):
        label = normalize_space(label)
        with self.lock:
            return self._spot_indices.get(lot_id, {}).get(label)

    def register_space(self, lot_id, label):
        """서버가 준 칸 이름이 명부에 없으면 뒤에 추가한다(서버 기준). 순번을 돌려준다."""
        label = normalize_space(label)
        if label is None:
            return None
        with self.lock:
            lot = self._lots.get(lot_id)
            if lot is None:
                return None
            index = self._spot_indices[lot_id].get(label)
            if index is not None:
                return index
            spaces = [*lot.get("spaces", []), label]
            self._publish({**self._lots, lot_id: {**lot, "spaces": spaces}}, self.source)
            return len(spaces)

    def adopt_spaces(self, lot_id, labels):
        """검증된 서버 칸 목록 전체로 실행 중 명부를 맞춘다. 칸 이름 순서도 서버를 따른다."""
        with self.lock:
            lot = self._lots.get(lot_id)
            if lot is not None and labels and lot.get("spaces") != list(labels):
                row = {**lot, "spaces": list(labels), "capacity": len(labels)}
                self._publish({**self._lots, lot_id: row}, self.source)

    def space_zone(self, lot_id, label):
        """도면 번호의 구역. 설정된 전용 칸 → 그 구역, 설정된 그 밖의 칸 → 잔여 구역, 명부에 없는 칸 → None."""
        defined = self._settings_lot(lot_id) or {}
        for key, labels in (defined.get("zone_layout") or {}).items():
            if label in labels:
                return key
        if label in defined.get("spaces", []):
            return next(z["key"] for z in self.settings["zones"] if z.get("remaining"))
        return None

    def neighbors(self, lot_id, spot):
        """같은 줄(같은 구역 글자)에서 번호가 하나 차이 나는 칸의 순번. 실제 거리는 아니다."""
        label = self.space_label(lot_id, spot)
        if label is None:
            return set()
        prefix, number = label.split("-")
        result = set()
        for delta in (-1, 1):
            index = self.space_index(lot_id, f"{prefix}-{int(number) + delta:02d}")
            if index:
                result.add(index)
        return result

    def is_near(self, lot_id, a, b):
        return a == b or b in self.neighbors(lot_id, a)

    def place(self, lot_id, spot):
        """'개발원주차장 B-06' 형식. 칸을 모르면 주차장 이름만."""
        label = self.space_label(lot_id, spot)
        return f"{self.name(lot_id)} {label}" if label else self.name(lot_id)

    def with_space(self, item):
        """lot_id·spot_no가 있는 dict에 space_id(도면 번호)를 붙인 새 dict."""
        if not isinstance(item, dict) or "spot_no" not in item:
            return item
        return {**item, "space_id": self.space_label(item.get("lot_id"), item.get("spot_no"))}

    def zone_spots(self, capacity=None, lot_id=None):
        """구역별 칸 순번. 명부에 없는 칸(서버가 추가한 칸)은 어느 구역에도 넣지 않는다."""
        result = {z["key"]: [] for z in self.settings["zones"]}
        for index, label in enumerate(self.spaces(lot_id), start=1):
            if capacity is not None and index > capacity:
                break
            zone = self.space_zone(lot_id, label)
            if zone is not None:
                result[zone].append(index)
        return result

    def rate_policy(self, lot_id):
        lot = self._settings_lot(lot_id) or {}
        return dict(lot["rate_policy"]) if lot.get("rate_policy") else None

    def adopt(self, lots, *, spaces=None):
        """검증·신선도 확인이 끝난 API 응답에만 호출한다."""
        with self.lock:
            fallback = {x["lot_id"]:x for x in self.settings["lots"]}
            incoming = {}
            for key, v in lots.items():
                previous = self._lots.get(key) or fallback.get(key, {})
                incoming[key] = {**fallback.get(key,{}), "lot_id":key,
                                 "name":v.get("name", fallback.get(key,{}).get("name",key)),
                                 "kind":v.get("kind", fallback.get(key,{}).get("kind","unknown")),
                                 "capacity":v["total"], "spaces": list((spaces or {}).get(key, previous.get("spaces", [])))}
            if incoming != self._lots or self.source != "backend":
                logger(__name__).info("주차장 명부를 백엔드 기준으로 갱신: %d", len(incoming))
                self._publish(incoming, "backend")

    def public_snapshot(self):
        with self.lock:
            return self._public

    def public(self):
        # Existing in-process callers are allowed to edit this detached view.
        return deepcopy(self.public_snapshot())

    def chips(self):
        return deepcopy(self.chip_snapshot())

    def chip_snapshot(self):
        with self.lock:
            return self._chips

    def _build_chips(self):
        lots = [(lot["lot_id"], lot["name"]) for lot in self._lots.values()]
        zones = [(z["key"], z["name"]) for z in self.settings["zones"]]
        result = {}
        for key, name in lots:
            result["lot_" + key] = {"label": name + "만 보기", "intent": "get_empty_spots", "params": {"lot_id": key}}
            for zone in [None] + [key for key, _ in zones]:
                suffix = ":" + zone if zone else ""
                params = {"lot_id": key, **({"zone": zone} if zone else {})}
                result["go:" + key + suffix] = {"label": name + "으로 안내", "intent": "set_destination", "params": params}
        for key, name in zones:
            result["zone:" + key] = {"label": name + " 구역 빈자리", "intent": "get_empty_spots", "params": {"zone": key}}
        return result



def load_catalog(path=None):
    path = Path(path) if path is not None else Path(__file__).with_name("lots.json")
    return Catalog(json.loads(path.read_text(encoding="utf-8")))
