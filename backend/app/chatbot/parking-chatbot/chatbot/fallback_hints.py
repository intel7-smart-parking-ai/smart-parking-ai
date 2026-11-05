"""Rank existing chips for a first unknown input; never execute an intent."""

# These terms were chosen from the task's examples before the holdout freeze.
HINTS = (
    (("요금", "주차비", "사용료", "계산", "정산"), ("get_fee", "pay_fee", "fee_rule")),
    (("어디", "잊", "찾"), ("find_my_car", "change_vehicle")),
    (("막대", "차단기", "출구", "못 나가"), ("exit_help",)),
    (("카드", "승인", "거절", "결제 오류"), ("payment_issue",)),
    (("직원", "관리자", "사람", "근무"), ("admin_help",)),
    (("그만", "취소"), ("cancel_destination", "cancel_action")),
)


def select(text, default_ids, definitions):
    compact = "".join(text.casefold().split())
    candidates = {}
    for terms, intents in HINTS:
        score = sum("".join(term.split()) in compact for term in terms)
        if not score:
            continue
        for intent in intents:
            for chip_id, chip in definitions.items():
                if chip.get("intent") == intent and intent != "call_admin":
                    candidates[chip_id] = max(score, candidates.get(chip_id, 0))
    ranked = sorted(candidates, key=lambda chip_id: -candidates[chip_id])[:3]
    return list(dict.fromkeys([*ranked, *default_ids]))[:len(default_ids)]
