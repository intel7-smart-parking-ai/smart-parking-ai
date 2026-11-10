"""로컬 턴 기록(MONITOR_BACKEND=local)으로 Langfuse 트레이스처럼 문제를 추적하는 리포트.

    python tools/trace_report.py turns.jsonl
    python tools/trace_report.py turns.jsonl --hours 6 --sheet .monitoring/failed_sheet.json
    python tools/trace_report.py turns.jsonl --price-in 1.0 --price-out 5.0     # 1백만 토큰당 달러(선택)

출력(.md): 요약 · 검토 이유별 · 경로 끝 단계별 · 시간(전체·호출별) · 토큰·비용 · 지시문 버전별 ·
           문제 턴 상세(앞 대화 2턴, 경로, 호출별 시간, 버려진 초안, 최종 답)
--sheet: 문제 턴을 tools/run_dialogs.py 질문지로 저장 → 고친 뒤 같은 질문으로 다시 돌려 비교.
기록 파일에는 사용자 질문 원문이 들어 있을 수 있다(MONITOR_CAPTURE_TEXT). Git에 올리지 않는다.
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _when(value):
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def load(paths):
    rows = []
    for path in paths:
        path = Path(path)
        for candidate in (path.with_suffix(path.suffix + ".1"), path):   # 크기 제한으로 넘긴 이전 파일 먼저
            if not candidate.is_file():
                continue
            for line in candidate.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("type") != "feedback":
                    rows.append(row)
    rows.sort(key=lambda r: r.get("at") or "")
    return rows


def text_of(value):
    if isinstance(value, dict):
        return value.get("text") if isinstance(value.get("text"), str) else (f"({value.get('chars')}자, 원문 미저장)" if "chars" in value else None)
    return value if isinstance(value, str) else None


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def route_end(row):
    route = (row.get("output") or {}).get("route") or ""
    return route.split(" > ")[-1] if route else "-"


def short(value, n=120):
    value = (value or "").replace("\n", " / ").replace("|", "¦")
    return value if len(value) <= n else value[:n] + "…"


def build(rows, top=20, price_in=None, price_out=None):
    events = [r for r in rows if not (r.get("input") or {}).get("text") and not (r.get("input") or {}).get("chip_id")]
    rows = [r for r in rows if r not in events]   # 대화 시작·폴링 이벤트는 질문이 아니다
    n = len(rows)
    out = []
    if not n:
        return "# 턴 기록 리포트\n\n기록이 없어요. MONITOR_BACKEND=local로 켰는지 확인하세요.\n", []
    agent_rows = [r for r in rows if (r.get("agent") or {}).get("status")]
    ok = sum(1 for r in agent_rows if r["agent"]["status"] in ("ok", "clarify"))
    problems = [r for r in rows if r.get("review")]
    first, last = rows[0].get("at"), rows[-1].get("at")
    out += ["# 턴 기록 리포트", "",
            f"기간 {first} ~ {last} · 질문 턴 {n}개 · 대화 {len({r.get('session') for r in rows})}개 · 이벤트 {len(events)}개 제외", "",
            "| 항목 | 값 |", "|---|---|",
            f"| 대화 AI까지 간 턴 | {len(agent_rows)} ({len(agent_rows) * 100 // n}%) |",
            f"| 그중 AI 답이 나간 턴 | {ok} ({ok * 100 // max(1, len(agent_rows))}%) |",
            f"| 검토가 필요한 턴 | {len(problems)} ({len(problems) * 100 // n}%) |", ""]

    reasons = Counter(reason for r in problems for reason in r["review"])
    out += ["## 검토 이유별", "", "| 이유 | 턴 수 |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in reasons.most_common()] + [""]

    ends = Counter(route_end(r) for r in rows)
    out += ["## 경로 끝 단계별", "", "| 끝 단계 | 턴 수 |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in ends.most_common(15)] + [""]

    lat = [r.get("latency_ms") for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    calls = [c for r in agent_rows for c in (r["agent"].get("calls") or []) if isinstance(c, dict)]
    out += ["## 시간", "", "| 구분 | 중앙값 | 90% | 최대 | 개수 |", "|---|---|---|---|---|",
            f"| 턴 전체(ms) | {pct(lat, .5)} | {pct(lat, .9)} | {max(lat) if lat else None} | {len(lat)} |"]
    for kind, label in (("select", "도구 고르기 호출(ms)"), ("answer", "답 쓰기 호출(ms)")):
        ms = [c["ms"] for c in calls if c.get("kind") == kind and isinstance(c.get("ms"), (int, float))]
        bad = sum(1 for c in calls if c.get("kind") == kind and c.get("status") not in ("ok", None))
        out.append(f"| {label} | {pct(ms, .5)} | {pct(ms, .9)} | {max(ms) if ms else None} | {len(ms)} (실패 {bad}) |")
    model_calls = [r["agent"].get("model_calls") for r in agent_rows if isinstance(r["agent"].get("model_calls"), int)]
    if model_calls:
        out.append(f"\n턴당 모델 호출 평균 {statistics.mean(model_calls):.2f}회, 시간 초과 재시도 "
                   f"{sum(r['agent'].get('retried_timeout') or 0 for r in agent_rows)}회")
    out.append("")

    tin = [r["agent"].get("input_tokens") or 0 for r in agent_rows]
    tout = [r["agent"].get("output_tokens") or 0 for r in agent_rows]
    if agent_rows:
        line = f"입력 토큰 평균 {statistics.mean(tin):,.0f} · 출력 토큰 평균 {statistics.mean(tout):,.0f}"
        if price_in is not None and price_out is not None:
            cost = (sum(tin) * price_in + sum(tout) * price_out) / 1_000_000
            line += f" · 추정 비용 합계 ${cost:.4f} (턴당 ${cost / len(agent_rows):.5f})"
        out += ["## 토큰", "", line, ""]

    by_prompt = defaultdict(lambda: [0, 0])
    for r in agent_rows:
        version = (r.get("prompts") or {}).get("conversation") or "-"
        by_prompt[version][0] += 1
        by_prompt[version][1] += r["agent"]["status"] in ("ok", "clarify")
    if by_prompt:
        out += ["## 지시문 버전별 (대화 AI)", "", "| conversation.md 버전 | AI까지 간 턴 | AI 답 |", "|---|---|---|"]
        out += [f"| `{k}` | {a} | {b} ({b * 100 // max(1, a)}%) |" for k, (a, b) in by_prompt.items()] + [""]

    history = defaultdict(list)
    for r in rows:
        history[r.get("session")].append(r)
    out += [f"## 문제 턴 상세 (최근 {min(top, len(problems))}개)", ""]
    sheet = []
    for i, r in enumerate(problems[-top:], 1):
        session_rows = history[r.get("session")]
        index = session_rows.index(r)
        before = [text_of((p.get("input") or {}).get("text")) for p in session_rows[max(0, index - 2):index]]
        before = [b for b in before if b]
        question = text_of((r.get("input") or {}).get("text")) or (r.get("input") or {}).get("chip_id") or "-"
        agent = r.get("agent") or {}
        out.append(f"### {i}. {short(question, 80)}")
        out.append("")
        out.append(f"- 시각 {r.get('at')} · {r.get('latency_ms')}ms · 이유 `{', '.join(r['review'])}`")
        if before:
            out.append(f"- 앞 대화: {' → '.join(short(b, 50) for b in before)}")
        out.append(f"- 경로: `{(r.get('output') or {}).get('route') or '-'}`")
        for step in r.get("route") or []:
            if isinstance(step, dict) and step.get("why"):
                out.append(f"  - {step.get('stage')}:{step.get('outcome')} — {short(str(step.get('why')), 140)}")
        if agent.get("calls"):
            out.append("- 모델 호출: " + " / ".join(
                f"{c.get('kind')} {c.get('ms')}ms(제한 {c.get('limit_s')}초) {c.get('status')}"
                + (f" → {','.join(c['tools'])}" if c.get("tools") else "") for c in agent["calls"]))
        if agent.get("prefetched"):
            out.append(f"- 미리 조회: {', '.join(agent['prefetched'])}")
        for d in agent.get("drafts") or []:
            out.append(f"- 버려진 초안({d.get('reason')}{', 고쳐 쓰기' if d.get('repair') else ''}): {short(text_of(d.get('text')), 200)}")
        if r.get("notice_dropped"):
            dropped = r["notice_dropped"]
            out.append(f"- 확정 안내({dropped.get('topic')})를 못 지켜 버려진 AI 답: {short(text_of(dropped.get('text')), 200)}")
        out.append(f"- 최종 답: {short(text_of((r.get('output') or {}).get('text')), 200)}")
        out.append("")
        if isinstance(question, str) and question != "-" and not question.startswith("("):
            sheet.append({"id": f"L{i:02d}", "kind": ", ".join(r["review"]), "turns": before + [question],
                          "expect": f"기록 {r.get('at')}: {', '.join(r['review'])} → 고친 뒤 AI 답·근거 통과 확인"})
    return "\n".join(out) + "\n", sheet


def main(argv=None):
    parser = argparse.ArgumentParser(description="로컬 턴 기록 리포트")
    parser.add_argument("files", nargs="*", default=[str(ROOT / ".monitoring" / "turns.jsonl")])
    parser.add_argument("--hours", type=float, help="최근 몇 시간만")
    parser.add_argument("--top", type=int, default=20, help="문제 턴 상세 개수(기본 20)")
    parser.add_argument("--out", help="리포트 경로(기본 .monitoring/trace_report_<시각>.md)")
    parser.add_argument("--sheet", help="문제 턴을 run_dialogs 질문지(JSON)로 저장할 경로")
    parser.add_argument("--price-in", type=float, help="입력 1백만 토큰당 달러(선택)")
    parser.add_argument("--price-out", type=float, help="출력 1백만 토큰당 달러(선택)")
    args = parser.parse_args(argv)
    rows = load(args.files)
    if args.hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
        rows = [r for r in rows if (_when(r.get("at")) or cutoff).astimezone(timezone.utc) >= cutoff]
    report, sheet = build(rows, args.top, args.price_in, args.price_out)
    out = Path(args.out) if args.out else ROOT / ".monitoring" / f"trace_report_{datetime.now():%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"리포트 → {out} (턴 {len(rows)}개, 문제 {len(sheet)}개)")
    if args.sheet:
        Path(args.sheet).write_text(json.dumps({"about": "trace_report가 뽑은 문제 턴", "dialogs": sheet},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"질문지 → {args.sheet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
