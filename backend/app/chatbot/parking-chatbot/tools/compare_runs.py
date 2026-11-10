"""두 실행 기록(run_dialogs.py 결과)을 질문별로 비교해 좋아짐·나빠짐·같음을 표로 낸다.

    python tools/compare_runs.py docs/eval/runs/A.json docs/eval/runs/B.json            # 화면에 마크다운
    python tools/compare_runs.py A.json B.json --out docs/eval/compare_A_B.md           # 파일로

판정 기준(eval_common.RANK): AI 답(1) > AI 조회·코드 문장 / 코드 담당(2) > 되묻기만(3) > 규칙 답으로 떨어짐(4) > 이해 못 함(5).
같은 등급이면 답 문장이 달라져도 '같음(문장 변경)'으로 표시한다 — 문장의 좋고 나쁨은 사람이 본다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import LABEL, RANK, load_run  # noqa: E402


def _key(row):
    return (row.get("id"), row.get("q"))


def _cell(text, size=70):
    return str(text or "").replace("|", "/").replace("\n", " / ")[:size]


def compare(before: dict, after: dict) -> tuple[str, dict]:
    b_rows = {_key(r): r for r in before["rows"]}
    a_rows = {_key(r): r for r in after["rows"]}
    counts = {"better": 0, "worse": 0, "same": 0, "same_text_changed": 0, "only_before": 0, "only_after": 0}
    lines = []
    for key in list(b_rows) + [k for k in a_rows if k not in b_rows]:
        b, a = b_rows.get(key), a_rows.get(key)
        if a is None:
            counts["only_before"] += 1
            continue
        if b is None:
            counts["only_after"] += 1
            lines.append(f"| {a['id']} | {_cell(a['q'], 28)} | — | {LABEL[a['grade']]} | 새 질문 | {_cell(a['a'])} |")
            continue
        rb, ra = RANK[b["grade"]], RANK[a["grade"]]
        if ra < rb:
            verdict, counts["better"] = "**좋아짐**", counts["better"] + 1
        elif ra > rb:
            verdict, counts["worse"] = "**나빠짐**", counts["worse"] + 1
        elif (a.get("a") or "").strip() != (b.get("a") or "").strip():
            verdict, counts["same_text_changed"] = "같음(문장 변경)", counts["same_text_changed"] + 1
        else:
            verdict, counts["same"] = "같음", counts["same"] + 1
        if verdict == "같음":
            continue
        lines.append(f"| {a['id']} | {_cell(a['q'], 28)} | {LABEL[b['grade']]} | {LABEL[a['grade']]} | {verdict} | "
                     f"{_cell(b.get('a'), 55)} → {_cell(a.get('a'), 55)} |")
    bm, am = before.get("meta", {}), after.get("meta", {})
    bs, as_ = before.get("summary", {}), after.get("summary", {})
    head = [f"# 실행 비교: {bm.get('label')} → {am.get('label')}", "",
            f"| | 전 | 후 |", "|---|---|---|",
            f"| 커밋 | {bm.get('commit')} | {am.get('commit')} |",
            f"| 모델 | {bm.get('model')} | {am.get('model')} |",
            f"| 시간 제한 | {(bm.get('settings') or {}).get('REQUEST_BUDGET_SEC', '?')}초 | {(am.get('settings') or {}).get('REQUEST_BUDGET_SEC', '?')}초 |",
            f"| AI 답 | {bs.get('ai_answers')}/{bs.get('turns')} | {as_.get('ai_answers')}/{as_.get('turns')} |",
            f"| 판정 분포 | {bs.get('grades')} | {as_.get('grades')} |",
            f"| 평균 / 최대 | {bs.get('avg_sec')}초 / {bs.get('max_sec')}초 | {as_.get('avg_sec')}초 / {as_.get('max_sec')}초 |", "",
            f"**좋아짐 {counts['better']} · 나빠짐 {counts['worse']} · 같음 {counts['same']} · 같음(문장 변경) {counts['same_text_changed']}**"
            + (f" · 새 질문 {counts['only_after']}" if counts["only_after"] else "")
            + (f" · 빠진 질문 {counts['only_before']}" if counts["only_before"] else ""), "",
            "| id | 질문 | 전 | 후 | 판정 | 답(전 → 후) |", "|---|---|---|---|---|---|"]
    return "\n".join(head + lines) + "\n", counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text, counts = compare(load_run(args.before), load_run(args.after))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{args.out} | {counts}")
    else:
        print(text)


if __name__ == "__main__":
    main()
