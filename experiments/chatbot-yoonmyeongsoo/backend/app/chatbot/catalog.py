import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

# D-57, D-59 및 실제 PARK:ON 시스템 규격
LOTS_CONFIG = {
    "A": {
        "lot_id": "A",
        "name": "개발원 주차장",
        "aliases": ["개발원", "개발원주차장", "본원", "에이", "A", "a"],
        "kind": "real",
        "capacity": 12,
        "spaces": [
            "A-01", "A-02", "A-03", "A-04", "A-05", "A-06",
            "B-01", "B-02", "B-03", "B-04", "B-05", "B-06"
        ],
        "ev_spaces": ["A-05", "A-06"],
        "accessible_spaces": ["B-06"],
        "fee_rule": {
            "base_min": 30,
            "base_fee": 1000,
            "unit_min": 10,
            "unit_fee": 500,
            "daily_max": 15000,
            "free_min": 15
        }
    },
    "B": {
        "lot_id": "B",
        "name": "부산역 주차장",
        "aliases": ["부산역", "부산역주차장", "역주차장", "비", "B", "b"],
        "kind": "real",
        "capacity": 16,
        "spaces": [
            "A-01", "A-02", "A-03", "A-04", "A-05", "A-06", "A-07", "A-08",
            "B-01", "B-02", "B-03", "B-04", "B-05", "B-06", "B-07", "B-08"
        ],
        "ev_spaces": ["A-07", "A-08"],
        "accessible_spaces": ["B-07", "B-08"],
        "fee_rule": {
            "base_min": 30,
            "base_fee": 1500,
            "unit_min": 10,
            "unit_fee": 600,
            "daily_max": 20000,
            "free_min": 15
        }
    },
    "C": {
        "lot_id": "C",
        "name": "김해공항 주차장",
        "aliases": ["김해공항", "김해공항주차장", "공항", "공항 주차장", "씨", "시", "C", "c"],
        "kind": "virtual",
        "capacity": 64,
        "sections": {
            "P1": ["A-01", "A-02", "A-03", "A-04", "A-05", "A-06", "A-07", "A-08", "A-09", "A-10", "A-11", "A-12", "A-13", "A-14", "A-15", "A-16", "A-17", "A-18", "A-19", "A-20", "A-21", "A-22", "A-23", "A-24"],
            "P2": ["B-01", "B-02", "B-03", "B-04", "B-05", "B-06", "B-07", "B-08", "B-09", "B-10", "B-11", "B-12", "B-13", "B-14", "B-15", "B-16", "B-17", "B-18", "B-19", "B-20", "B-21", "B-22", "B-23", "B-24"],
            "P3": ["B-25", "B-26", "B-27", "B-28", "B-29", "B-30", "B-31", "B-32", "B-33", "B-34", "B-35", "B-36", "C-01", "C-02", "C-03", "C-04"]
        },
        "spaces": [
            f"A-{i:02d}" for i in range(1, 25)
        ] + [
            f"B-{i:02d}" for i in range(1, 37)
        ] + [
            f"C-{i:02d}" for i in range(1, 5)
        ],
        "ev_spaces": ["A-11", "A-12", "B-11", "B-12", "B-31", "B-32"],
        "accessible_spaces": [f"A-{i:02d}" for i in range(1, 7)] + [f"B-{i:02d}" for i in range(1, 7)] + [f"B-{i:02d}" for i in range(25, 29)],
        "fee_rule": {
            "base_min": 30,
            "base_fee": 3000,
            "unit_min": 10,
            "unit_fee": 800,
            "daily_max": 40000,
            "free_min": 20
        }
    },
    "D": {
        "lot_id": "D",
        "name": "신세계백화점 주차장",
        "aliases": ["신세계", "신세계백화점", "신세계백화점주차장", "백화점", "디", "D", "d"],
        "kind": "virtual",
        "capacity": 64,
        "sections": {
            "A": [f"A-{i:02d}" for i in range(1, 17)],
            "B": [f"B-{i:02d}" for i in range(1, 17)],
            "C": [f"C-{i:02d}" for i in range(1, 17)],
            "D": [f"D-{i:02d}" for i in range(1, 17)]
        },
        "spaces": [
            f"{sec}-{i:02d}" for sec in ["A", "B", "C", "D"] for i in range(1, 17)
        ],
        "ev_spaces": [f"C-{i:02d}" for i in range(5, 9)] + [f"D-{i:02d}" for i in range(5, 9)],
        "accessible_spaces": [f"A-{i:02d}" for i in range(5, 9)] + [f"B-{i:02d}" for i in range(5, 9)],
        "fee_rule": {
            "base_min": 30,
            "base_fee": 2000,
            "unit_min": 10,
            "unit_fee": 700,
            "daily_max": 30000,
            "free_min": 30
        }
    }
}

# D-54 차량 번호 규격
EV_CARS = {"11", "23", "42", "57", "85"}
ACCESSIBLE_CARS = {"14", "32", "49", "55", "77"}

def get_lot(lot_id_or_name: str) -> Optional[Dict[str, Any]]:
    if not lot_id_or_name:
        return None
    key = lot_id_or_name.strip()
    if key in LOTS_CONFIG:
        return LOTS_CONFIG[key]
    for lot in LOTS_CONFIG.values():
        if key == lot["name"] or key in lot["aliases"]:
            return lot
    return None

def get_vehicle_type(car_id: str) -> str:
    cid = str(car_id).strip()
    if cid in EV_CARS:
        return "ev"
    if cid in ACCESSIBLE_CARS:
        return "accessible"
    return "general"

