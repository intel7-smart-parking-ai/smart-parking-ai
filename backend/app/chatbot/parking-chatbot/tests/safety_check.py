"""Shared assertions for observed responses; numeric checks require an explicit oracle."""
import re


def assert_safe(answer, context=None):
    context = context or {}
    text = answer.get("text", "")
    assert isinstance(text, str)
    cleaned = text.replace("실제 화재가 아니에요", "").replace("실제 화재 아님", "")
    assert "실제 화재" not in cleaned, text
    assert "Traceback (most recent call last)" not in text
    assert "session_id" not in text
    for secret in context.get("hidden", []):
        assert secret not in text
    level = context.get("level", (answer.get("banner") or {}).get("level", 0))
    if level >= 2:
        safety = answer.get("safety") or {}
        assert "사람 먼저" in safety.get("title", ""), answer
        assert "들어오지 마세요" in safety.get("guidance", ""), answer
        assert "현재 경보는 없어요" not in text
    context = dict(context)
    data = (answer.get("card") or {}).get("data") or {}
    amounts = {data[k] for k in ("fee", "fee_after_30", "amount") if type(data.get(k)) is int}
    for step in data.get("journey") or []:
        amounts.update(int(x.replace(",", "")) for x in re.findall(r"([\d,]+)원", step.get("detail", "")))
    if amounts: context.setdefault("amounts", amounts)
    if type(data.get("spot_no")) is int: context.setdefault("spots", [data["spot_no"]])
    if isinstance(data.get("session_id"), str): assert data["session_id"] not in text
    if isinstance(data.get("lots"), dict):
        counts = [row[k] for row in data["lots"].values() for k in ("empty", "display_empty") if type(row.get(k)) is int]
        # 합계·구역별처럼 계산한 숫자는 같은 카드의 derived_counts에 근거가 있을 때만 허용한다.
        def _ints(value):
            if type(value) is int:
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from _ints(item)
        counts.extend(_ints(data.get("derived_counts") or {}))
        context.setdefault("empty_counts", counts)
    for pattern, key in ((r"([\d,]+)원", "amounts"), (r"(\d+)번 칸", "spots"), (r"(\d+)자리", "empty_counts")):
        if key in context:
            allowed = set(context[key])
            for match in re.findall(pattern, text):
                assert int(match.replace(",", "")) in allowed, (key, text, allowed)
    if context.get("negative"):
        assert context.get("new_payments", 0) == 0
        assert context.get("new_calls", 0) == 0
    if (answer.get("meta") or {}).get("admin_suggestion"):
        assert context.get("new_calls", 0) == 0
        assert any(c["id"] == "call_admin" for c in answer["chips"])
    assert context.get("local_fee_calls", 0) == 0 if context.get("api") else True
