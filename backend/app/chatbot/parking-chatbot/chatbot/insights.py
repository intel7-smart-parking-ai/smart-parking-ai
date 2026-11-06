"""가진 데이터로 계산해 답하는 질문: 가장 여유 있는 곳, 두 곳 비교, 점유율, 주차장 안 구역별 빈자리, 요금 정책 비교.

방법론: 자연어 질문을 '집계 연산'(최댓값·비교·비율·필터)으로 바꾸고, 계산은 조회 결과(점유 스냅샷의
lots/zones/space_states, 요금 정책 /rate-policies)에만 적용한다(text-to-SQL을 작게 만든 형태).
- 경보·운영 제한·확인 불가 주차장은 순위에서 빼고 이유를 밝힌다(navigation.reason과 같은 판정).
- 카메라 확인 중 칸은 빈자리로 세지 않고 따로 알린다.
- 요금은 정책 값을 비교만 한다. 금액을 새로 계산하지 않는다(루트 CLAUDE.md 4번).
"""
from __future__ import annotations

from . import config
from . import navigation as N
from .templates import won


def _final(word: str) -> bool:
    last = word.rstrip()[-1:] if word.strip() else ""
    return "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0


def iga(word):
    return word + ("이" if _final(word) else "가")


def gwa(word):
    return word + ("과" if _final(word) else "와")


def eun(word):
    return word + ("은" if _final(word) else "는")


def _name(lot):
    return config.catalog.name(lot)


def _scope(zone):
    return f"{config.catalog.zone_name(zone)} 구역 " if zone else ""


def _usable(snapshot, alert, zone):
    """(순위 가능한 주차장 [(lot, 빈자리)], 제외된 주차장 [(lot, 사유)])"""
    ranked, excluded = [], []
    for lot in N.order_lots(snapshot):
        why = N.reason(snapshot, alert, lot, zone)
        n = N.count(snapshot["lots"][lot], zone)
        if why in (None, "만차") and n is not None:
            ranked.append((lot, n))
        else:
            excluded.append((lot, why or "정보 없음"))
    return ranked, excluded


def _short(why):
    return "경보 중" if why == "경보" else "이용 제한" if why.startswith("운영 제한") else "확인 불가"


def _excluded_note(excluded):
    if not excluded:
        return ""
    return "\n(" + ", ".join(f"{_name(k)} {_short(w)}" for k, w in excluded) + " — 순위에서 제외)"


def _spots(n):
    return f"{n}자리" if n else "만차"


def most(snapshot, alert, zone=None):
    """가장 여유 있는 주차장. 반환: text, focus(단독 1위만), offered(동률 목록), chips"""
    ranked, excluded = _usable(snapshot, alert, zone)
    if not ranked or max(n for _, n in ranked) == 0:
        text = f"지금은 {_scope(zone)}빈자리가 있는 주차장이 없어요." + _excluded_note(excluded)
        return text, None, [], ["empty_all", "call_admin"]
    ranked.sort(key=lambda x: -x[1])
    top = ranked[0][1]
    best = [k for k, n in ranked if n == top]
    item = snapshot["lots"][best[0]]
    total = (item if zone is None else item.get("zones", {}).get(zone, {})).get("total")
    verb = "가장 많아요" if zone else "가장 여유 있어요"
    if len(best) == 1:
        ratio = f"({total}칸 중 {round(top / total * 100)}% 비어 있음)" if total else ""
        head = f"지금 {_scope(zone)}빈자리는 {iga(_name(best[0]))} {top}자리로 {verb}{ratio}."
    else:
        names = ", ".join(_name(k) for k in best[:-1])
        head = f"지금 {_scope(zone)}빈자리는 {gwa(names)} {iga(_name(best[-1]))} 각각 {top}자리로 {verb}."
    rest = [(k, n) for k, n in ranked if k not in best]
    if rest:
        head += "\n다음은 " + " · ".join(f"{_name(k)} {_spots(n)}" for k, n in rest) + "예요."
    head += _excluded_note(excluded)
    chips = [N.go_id(k, zone) for k in best[:3]] + ["empty_all"]
    return head, (best[0] if len(best) == 1 else None), (best if len(best) > 1 else []), chips[:4]


def compare(snapshot, alert, lots, zone=None):
    rows = []
    for lot in lots:
        if lot not in snapshot["lots"]:
            rows.append((lot, None, "정보 없음"))
            continue
        why = N.reason(snapshot, alert, lot, zone)
        rows.append((lot, N.count(snapshot["lots"][lot], zone), why))
    parts = [f"{_name(k)} {_spots(n) if why in (None, '만차') and n is not None else _short(why or '')}" for k, n, why in rows]
    text = f"{_scope(zone)}빈자리: " + " · ".join(parts) + "."
    usable = [(k, n) for k, n, why in rows if why in (None, "만차") and n is not None]
    focus = None
    if len(usable) >= 2:
        top = max(n for _, n in usable)
        best = [k for k, n in usable if n == top]
        if top == 0:
            text += "\n두 곳 모두 만차예요." if len(usable) == 2 else "\n모두 만차예요."
        elif len(best) == 1:
            focus = best[0]
            text += f"\n{iga(_name(focus))} 더 여유 있어요."
        else:
            text += f"\n{', '.join(_name(k) for k in best)} 모두 {top}자리로 같아요."
    elif len(usable) == 1 and usable[0][1] > 0:
        focus = usable[0][0]
        text += f"\n지금 이용할 수 있는 곳은 {eun(_name(focus))} 쪽이에요."
    chips = ([N.go_id(focus, zone)] if focus else []) + ["lot_" + k for k, _, _ in rows if k != focus] + ["empty_all"]
    return text, focus, chips[:4]


def occupancy(snapshot, alert, lot=None, zone=None):
    lots = [lot] if lot else N.order_lots(snapshot)
    total = empty = uncertain = 0
    for key in lots:
        item = snapshot["lots"].get(key)
        if item is None:
            return "해당 주차장 정보가 아직 연결되지 않았어요.", ["empty_all"], {}
        why = N.reason(snapshot, alert, key, zone)
        if lot and why not in (None, "만차"):
            return N.status_sentence(N.label(key, zone), why), ["lot_" + key, "empty_all"], {}
        if why not in (None, "만차"):
            continue
        part = item if zone is None else item.get("zones", {}).get(zone) or {}
        total += part.get("total") or 0
        empty += part.get("empty") or 0
        uncertain += part.get("uncertain") or 0
    if not total:
        return "점유 정보를 지금 확인할 수 없어요.", ["empty_all"], {}
    occupied = max(total - empty - uncertain, 0)
    derived = {"total": total, "occupied": occupied, "total_empty": empty}
    who = eun(N.label(lot, zone)) if lot else f"전체 주차장 {_scope(zone)}".rstrip() + "은"
    text = f"{who} {total}칸 중 {occupied}칸이 차 있어요({round(occupied / total * 100)}%). 빈자리는 {empty}자리예요."
    if uncertain:
        text += f" (카메라 확인 중 {uncertain}칸)"
    chips = ([N.go_id(lot, zone)] if lot and empty else []) + ["empty_all", "find_car"]
    return text, chips[:4], derived


def _space_states(snapshot, lot):
    return (snapshot.get("space_states") or {}).get(lot) or {}


def section(snapshot, alert, lot, letter):
    name = _name(lot)
    item = snapshot["lots"].get(lot)
    if item is None:
        return "해당 주차장 정보가 아직 연결되지 않았어요.", ["empty_all"], {}
    why = N.reason(snapshot, alert, lot)
    if why not in (None, "만차"):
        return N.status_sentence(name, why), ["lot_" + lot, "empty_all"], {}
    labels = [x for x in config.catalog.spaces(lot) if x.startswith(letter + "-")]
    states = _space_states(snapshot, lot)
    known = {x: states[x] for x in labels if x in states}
    if not known:
        return (f"{name} {letter} 구역의 칸별 상태는 지금 확인할 수 없어요. {name} 전체 빈자리는 {item.get('empty')}자리예요.",
                ["lot_" + lot, "empty_all"], {})
    empty = [x for x, v in known.items() if v == "empty"]
    uncertain = sum(v == "uncertain" for v in known.values())
    text = f"{name} {letter} 구역은 {len(labels)}칸 중 {len(empty)}자리 비어 있어요."
    special = {"ev": 0, "accessible": 0}
    for x in empty:
        zone = config.catalog.space_zone(lot, x)
        if zone in special:
            special[zone] += 1
    notes = [f"{label} 전용 {special[key]}" for key, label in (("ev", "전기차"), ("accessible", "장애인")) if special[key]]
    if notes:
        text += f" (그중 {' · '.join(notes)}칸)"
    if uncertain:
        text += f" 카메라 확인 중 {uncertain}칸."
    if len(known) < len(labels):
        text += f" ({len(labels) - len(known)}칸은 상태 미확인)"
    return text, [N.go_id(lot), "lot_" + lot, "empty_all"], {"sections": {letter: len(empty)}}


def section_rank(snapshot, alert, lot):
    """한 주차장 안에서 가장 여유 있는 구역(칸 이름 앞 글자 기준)."""
    name = _name(lot)
    why = N.reason(snapshot, alert, lot)
    if why not in (None, "만차"):
        return N.status_sentence(name, why), ["lot_" + lot, "empty_all"], {}
    states = _space_states(snapshot, lot)
    groups = {}
    for label in config.catalog.spaces(lot):
        if label in states:
            groups.setdefault(label.split("-")[0], []).append(states[label])
    if len(groups) < 2:
        item = snapshot["lots"][lot]
        return f"{name}은 구역별로 나눠 비교할 정보가 없어요. 전체 빈자리는 {item.get('empty')}자리예요.", [N.go_id(lot), "empty_all"], {}
    counts = sorted(((k, sum(v == "empty" for v in vals)) for k, vals in groups.items()), key=lambda x: (-x[1], x[0]))
    top = counts[0][1]
    best = [k for k, n in counts if n == top]
    derived = {"sections": dict(counts)}
    if top == 0:
        return f"{name}은 지금 모든 구역이 만차예요.", ["empty_all", "call_admin"], derived
    head = (f"{name}에서는 {' · '.join(k + ' 구역' for k in best)}" + ("이" if len(best) == 1 else "이 각각")
            + f" {top}자리로 가장 여유 있어요.")
    rest = [f"{k} 구역 {_spots(n)}" for k, n in counts if k not in best]
    if rest:
        head += "\n" + " · ".join(rest) + "예요."
    return head, [N.go_id(lot), "lot_" + lot, "empty_all"], derived


def fee_rank(policies, high=False):
    """요금 정책 비교(기본요금 → 하루 최대 순). 정책 값만 보여 주고 금액을 새로 계산하지 않는다."""
    rows = [(k, p) for k, p in policies.items() if config.catalog.name(k)]
    if not rows:
        return "요금 정책을 확인할 수 없어요."
    rows.sort(key=lambda x: (x[1]["base_fee"], x[1]["daily_cap"]))
    pick, other = (rows[-1], rows[0]) if high else (rows[0], rows[-1])
    key = lambda p: (p["base_fee"], p["daily_cap"])  # noqa: E731
    same = [k for k, p in rows if key(p) == key(pick[1])]
    word, other_word = ("가장 비싸요", "가장 저렴한") if high else ("가장 저렴해요", "가장 비싼")
    p = pick[1]
    who = ", ".join(_name(k) for k in same)
    text = (f"기본요금 기준으로 {iga(who)} 처음 {p['base_minutes']}분 {won(p['base_fee'])}으로 {word}"
            f"(이후 {p['unit_minutes']}분마다 {won(p['extra_fee'])}, 하루 최대 {won(p['daily_cap'])}).")
    if len(rows) > 1 and key(other[1]) != key(p):
        text += f"\n{other_word} 곳은 {_name(other[0])} {won(other[1]['base_fee'])}이에요."
    text += "\n실제 요금은 입차 시각 기준으로 서버가 계산해요."
    return text
