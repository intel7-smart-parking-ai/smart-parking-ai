"""Search the service's approved rules and per-lot demonstration guide."""
from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..info_gaps import TOPICS


@dataclass(frozen=True)
class Rule:
    key: str
    terms: tuple[str, ...]
    text: str
    source: str


RULES = (
    Rule("restricted_spaces", ("전용", "일반차", "전기차", "충전", "장애인", "자격", "ev"),
         "일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. 충전 구역의 빈 칸 수는 현재 점유 조회에서 확인합니다.",
         "팀 결정 D-05"),
    Rule("uncertain_space", ("확인 중", "확인중", "미확정", "불확실", "uncertain"),
         "확인 중인 칸은 점유 여부가 확정되지 않은 칸입니다. 빈 칸으로 안내할 수 없습니다.",
         "docs/API_CONTRACT.md · 주차칸 상태"),
    Rule("vehicle_pending", ("칸 배정", "입차 확인", "위치 확인", "차가 없다", "차가 없"),
         "입차 확인 중인 차량은 번호가 확인됐지만 아직 주차칸이 배정되지 않았습니다.",
         "docs/API_CONTRACT.md · 차량 위치"),
    Rule("forecast", ("도착", "나중", "예측", "분 뒤", "분 후", "시간 뒤", "내일", "붐빌", "붐비", "혼잡"),
         "현재 내부 자료는 현재 빈자리를 보여 줍니다. 도착 시점의 빈자리나 대기시간을 보장하는 예측값은 제공되지 않습니다.",
         "chatbot/info_gaps.py · 혼잡 예측 범위"),
    Rule("payment", ("결제", "정산", "요금", "견적"),
         "요금과 견적은 서버에서 확인합니다. 모의 결제는 견적을 보여 주고 사용자가 승인한 뒤 진행합니다.",
         "docs/API_CONTRACT.md · 요금과 모의 결제"),
    Rule("capabilities", ("가능한", "할 수 있", "무엇을", "도움말", "사용법", "무슨 질문"),
         "현재 빈자리·차량 위치·요금 조회와 주차장 비교를 도와드립니다. 정해진 칩 또는 자연어로 질문할 수 있습니다.",
         "주차 챗봇 사용자 기능"),
)

_FACILITY_FIELDS = {
    "underground": ("실내", "실외", "노상", "지하"),
    "height_limit_m": ("높이", "차고", "진입 제한"),
    "sprinkler": ("스프링클러",),
}


TOPIC_KEYS = tuple(rule.key for rule in RULES) + ("facility",)


def _rule_row(record):
    return {"id": record.key, "text": record.text, "source": record.source}


def _facility_rows(lot_id, keys=None):
    """주차장 guide·facility에 등록된 문장. keys가 있으면 그 시설 주제만."""
    rows = []
    row = config.catalog.lot(lot_id) or {}
    guide = row.get("guide") or {}
    for key, label, _pattern in TOPICS:
        value = guide.get(key)
        if (keys is None or key in keys) and isinstance(value, str) and value.strip():
            rows.append({"id": key, "lot_id": lot_id, "text": f"시연용 설정 · {label}: {value.strip()}",
                         "source": "주차장 설정 guide." + key})
    # False and numeric zero are meaningful values; null means unregistered.
    facility = row.get("facility") or {}
    for key in _FACILITY_FIELDS:
        if keys is not None and key not in keys:
            continue
        value = facility.get(key)
        if key == "underground" and isinstance(value, bool):
            description = "지하 주차장입니다." if value else "지하 주차장이 아닙니다."
        elif key == "sprinkler" and isinstance(value, bool):
            description = "스프링클러가 있습니다." if value else "스프링클러가 없습니다."
        elif key == "height_limit_m" and type(value) in (float, int) and not isinstance(value, bool):
            description = f"진입 높이 제한은 {value}m입니다."
        else:
            continue
        rows.append({"id": key, "lot_id": lot_id, "text": f"시연용 설정 · {description}",
                     "source": "주차장 설정 facility." + key})
    return rows


def search(query: str, lots: list[str], topics=None):
    """(matches, unregistered). topics는 주제 전체를, query는 단어가 맞는 규칙·시설을 돌려준다.
    unregistered는 질문한 시설 주제 중 등록 문장이 없는 것의 화면 표기(예: '엘리베이터')."""
    query = query or ""
    topics = set(topics or ())
    normalized = "".join(query.casefold().split())
    found = [_rule_row(record) for record in RULES
             if record.key in topics or (normalized and (record.key in normalized or any(
                 "".join(term.split()) in normalized for term in record.terms)))]
    asked_facility = {key for key, _label, pattern in TOPICS if pattern.search(query) or (normalized and key in normalized)}
    asked_facility |= {key for key, terms in _FACILITY_FIELDS.items() if any(term in normalized for term in terms)}
    unregistered = []
    for lot_id in lots:
        if "facility" in topics:
            found.extend(_facility_rows(lot_id))
        elif asked_facility:
            found.extend(_facility_rows(lot_id, asked_facility))
    registered = {row["id"] for row in found}
    labels = dict((key, label) for key, label, _ in TOPICS)
    labels.update({"underground": "지하 여부", "height_limit_m": "진입 높이 제한", "sprinkler": "스프링클러"})
    for key in sorted(asked_facility - registered):
        unregistered.append(labels.get(key, key))
    # 같은 문장을 두 번 주지 않는다.
    unique, seen = [], set()
    for row in found:
        marker = (row["id"], row.get("lot_id"))
        if marker not in seen:
            seen.add(marker)
            unique.append(row)
    return unique, unregistered
