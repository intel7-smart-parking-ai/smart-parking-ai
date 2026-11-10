"""평가 기록 공통: 턴 하나의 자동 판정(grade)과 실행 요약, 실행 메타.

판정은 경로 기록(route)과 대화 AI 기록(agent)으로 코드가 매긴다. 사람 채점을 대신하지는 않고,
'AI가 자연스럽게 답했는가 / 코드가 맡은 질문인가 / 무엇 때문에 규칙 답으로 떨어졌는가'를 한눈에 보게 한다.

| grade | 뜻 | 좋은 순서 |
|---|---|---|
| ai | 대화 AI가 조회 결과로 직접 쓴 답 | 1 |
| ai_notice | 확정 안내를 근거로 AI가 쓴 답 | 1 |
| ai_render | AI가 조회는 했지만 글은 검사에 걸려 코드가 조회 결과로 다시 씀 | 2 |
| rule | 일부러 코드가 맡는 질문(안전·결제·승인·보안·충전 카드·혼잡 예측) | 2 (중립) |
| clarify | AI가 대상을 되물음(맥락 없는 '거기' 등) — 대화의 일부 | 1 |
| clarify_no_lookup | AI가 조회 없이 되묻기만 함 | 3 |
| fallback_ungrounded | AI 답이 근거 검사에 걸려 규칙 답 | 4 |
| fallback_timeout | 시간 초과로 규칙 답 | 4 |
| fallback_other | AI 꺼짐·키·한도 등으로 규칙 답 | 4 |
| fallback_notice | 확정 안내를 AI가 못 지켜 정해진 문장 | 4 |
| not_understood | '이해하지 못했어요' | 5 |
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

RANK = {"ai": 1, "ai_notice": 1, "ai_render": 2, "rule": 2, "clarify": 1, "clarify_no_lookup": 3,
        "fallback_ungrounded": 4, "fallback_timeout": 4, "fallback_other": 4, "fallback_notice": 4, "not_understood": 5}
LABEL = {"ai": "AI 답", "ai_notice": "AI 답(확정 안내)", "ai_render": "AI 조회·코드 문장", "rule": "코드 담당", "clarify": "AI 되물음",
         "clarify_no_lookup": "조회 없이 되묻기", "fallback_ungrounded": "검사 탈락→규칙", "fallback_timeout": "시간 초과→규칙",
         "fallback_other": "AI 불가→규칙", "fallback_notice": "확정 안내→정해진 문장", "not_understood": "이해 못 함"}
INTENDED_RULE = ("guard:emergency", "workflow:", "boundary:sales", "boundary:other_vehicle", "boundary:prompt_injection",
                 "rule:charging_status", "rule:congestion_forecast", "chip:", "start:")


def grade(row: dict) -> str:
    route = row.get("route") or ""
    steps = route.split(" > ")
    agent = row.get("agent") or {}
    last = steps[-1] if steps else ""
    if last == "agent:answer_with_notice":
        return "ai_notice"
    if last == "agent:clarify":
        return "clarify"
    if last == "agent:answer":
        rendered = any(s.get("stage") == "agent" and s.get("source") == "rule_render" for s in (row.get("steps") or []))
        if rendered or "agent:ungrounded" in steps:
            return "ai_render"
        if agent.get("status") == "ok" and not agent.get("tool_calls") and not agent.get("prefetched") and (row.get("a") or "").rstrip().endswith(("?", "？")):
            return "clarify_no_lookup"
        return "ai"
    if last == "fallback:unknown" or last == "rule:unknown":
        return "not_understood"
    if last.startswith("fallback:"):
        if "agent:timeout" in steps:
            return "fallback_timeout"
        if "agent:ungrounded" in steps or "agent:dropped" in steps and agent.get("status") == "ungrounded":
            return "fallback_ungrounded"
        return "fallback_other"
    if last.startswith("rule:") and any(s.startswith("agent:") for s in steps):
        return "fallback_notice"   # 확정 안내 질문에서 AI 답이 안내를 안 지켜 정해진 문장으로
    return "rule"


def summarize(rows: list) -> dict:
    counts = {}
    for row in rows:
        counts[row["grade"]] = counts.get(row["grade"], 0) + 1
    secs = [r["sec"] for r in rows if isinstance(r.get("sec"), (int, float))]
    ai = sum(counts.get(k, 0) for k in ("ai", "ai_notice"))
    judged = [r for r in rows if r["grade"] != "rule"]
    return {"turns": len(rows), "ai_answers": ai, "ai_ratio_excluding_rule": round(ai / len(judged), 2) if judged else None,
            "grades": dict(sorted(counts.items(), key=lambda kv: RANK[kv[0]])),
            "avg_sec": round(sum(secs) / len(secs), 1) if secs else None, "max_sec": max(secs) if secs else None}


def run_meta(question_file: str, label: str | None, root: Path) -> dict:
    from chatbot import config
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:
        commit, dirty = "unknown", None
    model = config.LLM_MODEL if config.LLM_PROVIDER != "none" else None
    return {"label": label or Path(question_file).stem, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "commit": commit + ("+" if dirty else ""), "model": model or "(모델 꺼짐)", "provider": config.LLM_PROVIDER,
            "question_file": str(Path(question_file).name),
            "settings": {"REQUEST_BUDGET_SEC": config.REQUEST_BUDGET_SEC, "LLM_ANSWER_TIMEOUT_SEC": config.LLM_ANSWER_TIMEOUT_SEC,
                         "LLM_MAX_OUTPUT_TOKENS": getattr(config, "LLM_MAX_OUTPUT_TOKENS", None),
                         "LLM_REPAIR_MIN_SEC": getattr(config, "LLM_REPAIR_MIN_SEC", None), "DATA_SOURCE": config.DATA_SOURCE}}


def load_run(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):   # 예전 형식(행 목록만)
        data = {"meta": {"label": Path(path).stem, "commit": "?", "model": "?"}, "rows": data}
    # 판정 기준이 바뀌어도 옛 기록과 새 기록을 같은 잣대로 보도록, 읽을 때 다시 판정한다
    for r in data["rows"]:
        r["grade"] = grade(r)
    data["summary"] = summarize(data["rows"])
    return data
