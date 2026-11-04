"""LLM 분류기 (선택). LLM_PROVIDER=none 이면 사용하지 않는다.

LLM은 문장을 {"intent": ..., "params": {...}} JSON으로 분류만 한다.
숫자·답변 문장은 절대 LLM이 만들지 않는다 (templates.py 담당).
실패하거나 LLM_TIMEOUT_SEC 안에 응답이 없으면 None → 규칙/칩 안내로 넘어간다.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
import urllib.request

from . import config
from .diagnostics import logger
from .parser import KNOWN_INTENTS
from .adapters import request_timeout
from .transport import load_json

_PROMPT_TEMPLATE = """너는 주차장 안내 챗봇의 '질문 분류기'다. 답변 문장을 만들지 말고 JSON 한 줄만 출력한다.
허용 intent: {intents}
부정 요청은 cancel_action, 문의 상태는 call_status, 결제 장애는 payment_issue,
결제수단 질문은 payment_help, 관리자 이용방법/비용 질문은 admin_help.
질문과 부정을 call_admin/pay_fee 실행으로 분류하지 않는다.
params: lot_id({lot_ids}), car_id({vmin}~{vmax} 두 자리 문자열). 모르면 넣지 않는다.
주차장 이름과 별칭: {lot_names}
주차장과 무관한 질문(날씨, 잡담 등)은 unknown.
출력 예: {{"intent":"get_empty_spots","params":{{"lot_id":"{example_lot}"}}}}"""


def system_prompt() -> str:
    """호출할 때마다 현재 주차장 명부(lots.json 또는 백엔드 명부)로 지시문을 만든다."""
    lots = list(config.catalog.lots().values())
    names = []
    for lot in lots:
        labels = [lot.get("name", lot["lot_id"])] + list(lot.get("aliases", []))
        names.append(f'{lot["lot_id"]}({"·".join(labels)})')
    return _PROMPT_TEMPLATE.format(
        intents=", ".join(sorted(KNOWN_INTENTS - {"confirm_yes", "confirm_no"})),
        lot_ids="/".join(lot["lot_id"] for lot in lots),
        vmin=f"{config.VEHICLE_MIN:02d}", vmax=f"{config.VEHICLE_MAX:02d}",
        lot_names=", ".join(names),
        example_lot=lots[1]["lot_id"] if len(lots) > 1 else lots[0]["lot_id"],
    )


def _post_json(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    return load_json(req, request_timeout(min(3.0, config.LLM_TIMEOUT_SEC)))


def _extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def classify(text: str) -> dict | None:
    provider = config.LLM_PROVIDER
    if provider == "none" or not config.LLM_MODEL or not config.api_key(provider):
        return None
    from . import llm_limits, llm_router

    started = time.monotonic()
    settings, reason = llm_router._settings()
    if reason:
        return None
    key = llm_limits.limits.key(text, ("direct", settings))
    cached = llm_limits.limits.lookup(key)
    if cached is not None:
        llm_router._record("cache_hit", started)
        return cached
    if not llm_router._slots.acquire(blocking=False):
        return None
    try:
        request_timeout(min(3.0, config.LLM_TIMEOUT_SEC))
        if not llm_limits.limits.admit(llm_limits.session_id.get()):
            llm_router._record("rate_limited", started)
            return None
        value = _classify_provider(provider, text)
        if isinstance(value, dict) and isinstance(value.get("intent"), str):
            reason, verified = llm_router._decode(SimpleNamespace(
                tool_calls=[{"name": value["intent"], "args": value.get("params") or {}}]
            ))
            if reason == "ok" and verified == value:
                llm_limits.limits.save(key, verified)
        return value
    except TimeoutError:
        return None
    finally:
        llm_router._slots.release()


def _classify_provider(provider, text):
    try:
        if provider == "anthropic":
            data = _post_json(
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": config.api_key("anthropic"), "anthropic-version": "2023-06-01"},
                {"model": config.LLM_MODEL, "max_tokens": 100, "temperature": 0,
                 "system": system_prompt(), "messages": [{"role": "user", "content": text}]},
            )
            return _extract_json(data["content"][0]["text"])
        if provider in ("openai", "openrouter"):
            body = {"model": config.LLM_MODEL, "temperature": 0, "max_tokens": 100,
                    "messages": [{"role": "system", "content": system_prompt()}, {"role": "user", "content": text}]}
            if provider == "openai":
                # OpenRouter 모델은 json_object 지원이 모델마다 달라 넣지 않는다. 본문 JSON은 _extract_json이 찾는다.
                body["response_format"] = {"type": "json_object"}
            url = ("https://api.openai.com/v1" if provider == "openai" else config.OPENROUTER_BASE_URL) + "/chat/completions"
            data = _post_json(url, {"Authorization": f"Bearer {config.api_key(provider)}"}, body)
            return _extract_json(data["choices"][0]["message"]["content"])
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        logger(__name__).error("Direct LLM classify failed (%s)", type(exc).__name__)
        return None
    return None
