"""가로채기 확인을 켠 채 여러 명이 동시에 묻는 상황(가짜 모델·가짜 확인, 키 불필요).

    python tools/route_check_load.py . [모델 지연 2.0] [확인 지연 1.0]
구성 3가지(끔 / 차례로 / 추천=빠른 길+동시)를 같은 문장 묶음으로 돌려 답 종류·시간·버린 호출·남은 스레드를 본다.
"""
import asyncio
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1])
MODEL_DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
CHECK_DELAY = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
os.environ.update(MONITOR_BACKEND="off", LLM_PROVIDER="none")
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from unittest.mock import patch  # noqa: E402

from langchain_core.messages import AIMessage  # noqa: E402
from chatbot import agents, config, engine as e, llm_limits, route_check  # noqa: E402
from chatbot.agents import conversation  # noqa: E402
from test_agent_runtime_v3 import ScriptedModel  # noqa: E402

# (종류, 문장, 확인이 내야 할 답)
KINDS = [
    ("AI 질문(확인 대상 아님)", "공항 자리 있어?", None),
    ("확실한 명령", "개발원 주차장으로 안내해줘", True),
    ("잘못 읽히는 질문", "공항 주차장으로 가는 셔틀 있어?", False),
    ("애매하지만 규칙이 맞는 문장", "김해공항 3시에 혼잡하나?", True),
]
VERDICT = {text: verdict for _, text, verdict in KINDS}
model_calls = Counter()


def model_factory():
    async def answer(messages):
        model_calls["started"] += 1
        await asyncio.sleep(MODEL_DELAY)
        model_calls["finished"] += 1
        return AIMessage(content="확인했어요.")
    return ScriptedModel([answer] * 6)


def fake_ask(text, intent, timeout):
    if CHECK_DELAY > timeout:
        time.sleep(timeout)
        raise TimeoutError("simulated")
    time.sleep(CHECK_DELAY)
    return VERDICT.get(text)


def classify(route):
    if "agent:answer" in route:
        return "AI 답"
    rule = [s for s in route if s.startswith(("rule:", "workflow:", "fallback:"))]
    return "규칙 답(" + (rule[-1] if rule else route[-1] if route else "?") + ")"


def ask(sid, text):
    started = time.perf_counter()
    try:
        r = e.handle(sid, text=text)
        return classify(r["meta"].get("route", "").split(" > ")), time.perf_counter() - started
    except Exception as exc:
        return "오류:" + type(exc).__name__, time.perf_counter() - started


def run(users, check, fast, parallel):
    e.reset_all()
    route_check.reset_for_tests()
    llm_limits.limits.turns.clear()
    llm_limits.limits.minute.clear()
    llm_limits.limits.daily_calls = 0
    model_calls.clear()
    results = defaultdict(list)
    run_conv = agents.run_conversation
    patches = [patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"),
               patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"),
               patch.object(config, "LLM_ROUTE_CHECK", check), patch.object(config, "LLM_ROUTE_CHECK_FAST", fast),
               patch.object(config, "LLM_ROUTE_CHECK_PARALLEL", parallel),
               patch.object(route_check, "_ask", side_effect=fake_ask),
               patch.object(agents, "run_conversation", side_effect=lambda *a, **k: run_conv(*a, model=model_factory(), **k))]
    for p in patches:
        p.start()
    try:
        def work(i):
            kind, text, _ = KINDS[i % len(KINDS)]
            results[kind].append(ask(f"user{i}", text))
        threads = [threading.Thread(target=work, args=(i,)) for i in range(users)]
        started = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.perf_counter() - started
        time.sleep(0.3)
    finally:
        for p in patches:
            p.stop()
    leftover = [t.name for t in threading.enumerate() if t.name == "route-check"]
    return results, wall, dict(route_check.stats()), dict(model_calls), len(llm_limits.limits.turns), leftover, conversation._active_models


CONFIGS = [("끔", False, False, False), ("차례로", True, False, False), ("추천(빠른 길+동시)", True, True, True)]
print(f"모델 지연 {MODEL_DELAY}초 · 확인 지연 {CHECK_DELAY}초 · 동시 모델 자리 {config.LLM_MAX_INFLIGHT} · 확인 자리 {config.LLM_ROUTE_CHECK_MAX_INFLIGHT}"
      f" · 제한 차례 {config.LLM_ROUTE_CHECK_TIMEOUT_SEC}초/동시 {config.LLM_ROUTE_CHECK_PARALLEL_TIMEOUT_SEC}초")
for users in (4, 8, 16):
    print(f"\n=== 동시 {users}명 ===")
    for name, check, fast, parallel in CONFIGS:
        results, wall, stats, calls, turns, leftover, active = run(users, check, fast, parallel)
        print(f"[{name}] 전체 {wall:.1f}초 · 확인 {stats} · 모델 호출 시작 {calls.get('started', 0)}/끝 {calls.get('finished', 0)}"
              f" · 한도에 센 턴 {turns} · 남은 확인 스레드 {len(leftover)} · 도는 모델 {active}")
        for kind, _, _ in KINDS:
            rows = results.get(kind) or []
            kinds = dict(Counter(r[0] for r in rows))
            secs = [r[1] for r in rows]
            print(f"   {kind}: {kinds} · 평균 {sum(secs) / len(secs):.1f}초 최대 {max(secs):.1f}초" if rows else f"   {kind}: -")
