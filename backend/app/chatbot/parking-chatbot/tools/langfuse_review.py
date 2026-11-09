"""Langfuse에서 '다시 볼 답'을 모아 검토 파일로 만든다.

모으는 것
- needs-review 태그가 붙은 턴: 이해 못 함, 모델 답 대체(시간 초과·근거 실패), 등록되지 않은 주차장 판정, 6초 넘는 응답
- 사용자 👎(user_feedback = 0) 점수가 붙은 턴과 남긴 의견

결과는 .monitoring/(Git 제외)에 마크다운과 JSON으로 저장한다. JSON의 cases는 회귀 테스트 질문 후보다.
키는 환경 변수(LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST)에서만 읽고 출력하지 않는다.

    python tools/langfuse_review.py --hours 24
    python tools/langfuse_review.py --hours 72 --env-file ../../../../.env
    python tools/langfuse_review.py --local .monitoring/turns.jsonl   # MONITOR_BACKEND=local 기록에서

참고: Langfuse Cloud의 GET /api/public/traces는 2026-11-16에 없어진다고 API 응답에 안내돼 있다.
그 뒤에는 /api/public/v2/observations로 옮겨야 한다(시연 11/13까지는 그대로 동작).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".monitoring"


def _load_env(path):
    if not path:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        sys.exit("python-dotenv가 없어 --env-file을 읽을 수 없어요. 환경 변수로 키를 넣어 주세요.")
    load_dotenv(path, override=False)


def _client():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        sys.exit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY가 설정되지 않았어요.")
    from langfuse import Langfuse
    return Langfuse(public_key=os.getenv("LANGFUSE_PUBLIC_KEY"), secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                    host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))


def _pages(fetch, limit=100, max_pages=20):
    for page in range(1, max_pages + 1):
        result = fetch(page=page, limit=limit)
        rows = list(getattr(result, "data", []) or [])
        yield from rows
        if len(rows) < limit:
            return


def _text(value):
    if isinstance(value, dict):
        return value.get("text") if isinstance(value.get("text"), str) else None
    return value if isinstance(value, str) else None


def _row(trace, feedback=None):
    out = trace.output if isinstance(trace.output, dict) else {}
    meta = trace.metadata if isinstance(trace.metadata, dict) else {}
    agent = meta.get("agent") or {}
    question = _text(trace.input)
    if question is None and isinstance(trace.input, dict):
        question = trace.input.get("chip_id") or trace.input.get("event")
    return {
        "trace_id": trace.id,
        "at": trace.timestamp.isoformat() if getattr(trace, "timestamp", None) else None,
        "session": trace.session_id,
        "question": question,
        "answer": _text(out),
        "intent": out.get("intent"), "source": out.get("source"), "boundary": out.get("boundary"),
        "agent_status": out.get("agent_status"), "grounding_error": out.get("grounding_error"),
        "repair_failed": agent.get("repair_failed"), "tools": meta.get("tools"),
        "latency_ms": meta.get("latency_ms"), "review": meta.get("review") or [],
        "route": _route(meta.get("route")) or out.get("route"), "feedback": feedback,
    }


def collect(client, since):
    rows = {}
    for trace in _pages(lambda **kw: client.api.trace.list(tags=["needs-review"], from_timestamp=since, **kw)):
        rows[trace.id] = _row(trace)
    downs = _pages(lambda **kw: client.api.score_v_2.get(name="user_feedback", operator="=", value=0,
                                                          from_timestamp=since, **kw))
    for score in downs:
        trace_id = getattr(score, "trace_id", None)
        if not trace_id:
            continue
        note = {"rating": "down", "comment": getattr(score, "comment", None)}
        if trace_id in rows:
            rows[trace_id]["feedback"] = note
            continue
        try:
            rows[trace_id] = _row(client.api.trace.get(trace_id), note)
        except Exception as exc:  # 지워진 trace 등
            rows[trace_id] = {"trace_id": trace_id, "feedback": note, "error": type(exc).__name__}
    return sorted(rows.values(), key=lambda r: r.get("at") or "", reverse=True)


def collect_local(path, since):
    """MONITOR_BACKEND=local 기록(JSON 한 줄씩)에서 다시 볼 턴과 👎를 모은다."""
    turns, downs = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        try:
            at = datetime.strptime(row.get("at", ""), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            at = None
        if at and at < since:
            continue
        if "feedback" in row:
            downs.append(row) if row.get("feedback") == "down" else None
            continue
        out = row.get("output") or {}
        item = {"trace_id": f"{row.get('session')}:{row.get('request_id') or row.get('at')}", "at": row.get("at"),
                "session": row.get("session"), "question": _text((row.get("input") or {}).get("text")) or (row.get("input") or {}).get("chip_id"),
                "answer": _text(out.get("text")), "intent": out.get("intent"), "source": out.get("source"),
                "boundary": out.get("boundary"), "agent_status": out.get("agent_status"),
                "grounding_error": out.get("grounding_error"), "repair_failed": (row.get("agent") or {}).get("repair_failed"),
                "tools": row.get("tools"), "latency_ms": row.get("latency_ms"), "review": row.get("review") or [],
                "route": _route(row.get("route")) or out.get("route"),
                "feedback": None, "_key": (row.get("session"), row.get("request_id"))}
        turns[item["trace_id"]] = item
    for down in downs:
        match = next((r for r in turns.values() if r["_key"] == (down.get("session"), down.get("request_id"))), None)
        if match is None:   # request_id 없이 보낸 피드백은 그 세션의 마지막 답
            match = next((r for r in reversed(list(turns.values())) if r["session"] == down.get("session")), None)
        if match:
            match["feedback"] = {"rating": "down", "comment": down.get("comment")}
    rows = [r for r in turns.values() if r["review"] or r["feedback"]]
    for r in rows:
        r.pop("_key", None)
    return sorted(rows, key=lambda r: r.get("at") or "", reverse=True)


def _route(steps):
    """경로 단계 목록 → 'guard:pass > agent:timeout > fallback:get_empty_spots'."""
    if not isinstance(steps, list):
        return None
    return " > ".join(f"{s.get('stage')}:{s.get('outcome')}" for s in steps if isinstance(s, dict)) or None


def _reasons(row):
    reasons = list(row.get("review") or [])
    if row.get("feedback"):
        reasons.append("user_down")
    return reasons or ["unknown"]


def write(rows, since, host):
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    counts = Counter(reason.split(":")[0] for row in rows for reason in _reasons(row))
    lines = [f"# 챗봇 검토 목록 ({stamp})", "",
             f"- 기간: {since.astimezone().strftime('%m/%d %H:%M')} 이후 · {len(rows)}건",
             "- 이유: " + (", ".join(f"{k} {v}" for k, v in counts.most_common()) or "없음"), "",
             "| 시각 | 질문 | 답 | 이유 | 거친 단계 |", "|---|---|---|---|---|"]
    for row in rows:
        def cell(value, size=90):
            return str(value or "").replace("|", "/").replace("\n", " / ")[:size]
        at = (row.get("at") or "")[5:16].replace("T", " ")
        link = f"[{at}]({host.rstrip('/')}/trace/{row['trace_id']})" if host else at
        why = ", ".join(_reasons(row))
        if row.get("feedback") and row["feedback"].get("comment"):
            why += f" · 의견: {cell(row['feedback']['comment'], 60)}"
        lines.append(f"| {link} | {cell(row.get('question'), 60)} | {cell(row.get('answer'))} | {cell(why, 80)} | "
                     f"{cell(row.get('route') or (str(row.get('source')) + '/' + str(row.get('agent_status') or '-')), 120)} |")
    lines += ["", "다음 단계: 고칠 질문을 `tests/dialogs` 또는 회귀 테스트에 옮기고, 같은 질문으로 다시 확인한다."]
    md = OUT_DIR / f"review_{stamp}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cases = [{"question": r.get("question"), "bad_answer": r.get("answer"), "reasons": _reasons(r),
              "trace_id": r["trace_id"]} for r in rows if r.get("question")]
    js = OUT_DIR / f"review_{stamp}.json"
    js.write_text(json.dumps({"since": since.isoformat(), "rows": rows, "cases": cases},
                             ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return md, js, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24, help="최근 몇 시간(기본 24)")
    parser.add_argument("--env-file", help="키를 읽을 .env 경로(선택)")
    parser.add_argument("--local", help="Langfuse 대신 읽을 로컬 기록 파일(MONITOR_LOCAL_PATH)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    if args.local:
        rows, host = collect_local(args.local, since), None
    else:
        _load_env(args.env_file)
        rows, host = collect(_client(), since), os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    md, js, counts = write(rows, since, host)
    print(f"{len(rows)}건 → {md}")
    for reason, count in counts.most_common():
        print(f"  {reason}: {count}")
    print(f"테스트 후보: {js}")


if __name__ == "__main__":
    main()
