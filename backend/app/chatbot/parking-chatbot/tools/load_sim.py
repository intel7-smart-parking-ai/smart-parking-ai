"""동시 질문 수·연속 질문 수에 따른 AI 답 비율 실험. 모델은 가짜(호출당 지연)라 키가 필요 없다.

    python tools/load_sim.py 2.0     # 동시 2·4·6·8·12명이 한 번에 질문(모델 지연 2초)
    python tools/load_sim.py 0.05    # 한 사람 연속 8질문, 12명이 번갈아 36질문(1분 한도 확인)

'규칙 답(busy)'는 동시 모델 호출 자리 부족, '규칙 답(rate_limited)'는 1분 질문 한도. 한도는 config.py·오버레이에서 조정.
"""
import asyncio
import json
import os
import sys
import threading
import time
from collections import Counter

os.environ.update(MONITOR_BACKEND="off")
ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from chatbot import agents, config, engine as e, llm_limits
from test_agent_runtime_v3 import ScriptedModel

DELAY = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0


def model_factory():
    async def answer(messages):
        await asyncio.sleep(DELAY)
        rows = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
        row = rows[0]["data"]["rows"][0]
        return AIMessage(content=f"{row['name']}에는 지금 {row['empty']}자리가 비어 있어요.")
    return ScriptedModel([answer] * 6)


def ask(sid, text="공항 자리 있어?"):
    started = time.perf_counter()
    try:
        r = e.handle(sid, text=text)
    except Exception as exc:
        return ["error:" + type(exc).__name__], time.perf_counter() - started
    return r["meta"].get("route", "").split(" > "), time.perf_counter() - started


def classify(route):
    agent = [s for s in route if s.startswith("agent:")]
    if route and route[-1] == "agent:answer":
        return "AI 답"
    return "규칙 답(" + (agent[0].split(":")[1] if agent else route[-1]) + ")"


def reset():
    e.reset_all()
    llm_limits.limits.turns.clear()
    llm_limits.limits.minute.clear()
    llm_limits.limits.daily_calls = 0


def concurrent(n):
    reset()
    results = [None] * n

    def work(i):
        results[i] = ask(f"user{i}")
    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return dict(Counter(classify(r[0]) for r in results)), max(r[1] for r in results)


_run = agents.run_conversation
patches = [patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"),
           patch.object(config, "api_key", return_value="test-only"),
           patch.object(agents, "run_conversation", side_effect=lambda *a: _run(*a, model=model_factory()))]
for p in patches:
    p.start()
try:
    print(f"설정: 동시 모델 호출 {config.LLM_MAX_INFLIGHT}(대기 {config.LLM_SLOT_WAIT_SEC}초), 분당 전체 {config.LLM_MAX_PER_MIN}/세션 "
          f"{config.LLM_MAX_PER_SESSION_MIN}, 예산 {config.REQUEST_BUDGET_SEC}초, 모델 지연 {DELAY}초")
    if DELAY > 0.2:
        for n in (2, 4, 6, 8, 12):
            kinds, slow = concurrent(n)
            print(f"동시 {n:2d}명 → {kinds} | 가장 느린 답 {slow:.1f}초")
    else:
        reset()
        print("한 사람이 연속 8질문 →", [classify(ask("one")[0]) for _ in range(8)])
        reset()
        seq = [classify(ask(f"u{i % 12}")[0]) for i in range(36)]
        print("12명이 번갈아 36질문(1분 안) → AI 답", seq.count("AI 답"), "/ 36,", Counter(s for s in seq if s != "AI 답"))
finally:
    for p in patches:
        p.stop()
