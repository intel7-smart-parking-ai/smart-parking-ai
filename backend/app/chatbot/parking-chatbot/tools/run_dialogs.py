"""대화 묶음(tests/dialogs/*.json의 dialogs)을 모델 켠 상태로 돌려 답·경로·시간을 표로 남긴다.

    python tools/run_dialogs.py tests/dialogs/tricky_demo.json --out .monitoring/tricky_run.json
    python tools/run_dialogs.py tests/dialogs/tricky_demo.json --env-file ../../../../.env
    # 변경 전 코드와 비교: 다른 체크아웃(git worktree)의 챗봇 폴더를 --root로
    python tools/run_dialogs.py tests/dialogs/tricky_demo.json --root ../../../../../sp-base/backend/app/chatbot/parking-chatbot

- 데이터는 mock, 모니터링은 로컬 임시 파일(경로 단계별 이유를 읽으려고)로 고정한다.
- 키는 환경 변수 또는 --env-file에서만 읽고 출력하지 않는다.
- 결과 JSON: {"meta": 실행 메타(커밋·모델·설정·날짜), "summary": 자동 판정 집계, "rows": 턴 목록}
  각 턴: id, kind, q, a, route(요약), steps(단계별 이유), agent(호출 수·토큰·시간), sec, expect, grade(자동 판정)
- --label 이름 --save: docs/eval/runs/<날짜>_<이름>.json 에 보관(평가 장부 docs/eval/EVAL_LOG.md 와 함께 커밋)
- 두 실행 비교: python tools/compare_runs.py <전.json> <후.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--out", default=str(ROOT / ".monitoring" / "dialog_run.json"))
    ap.add_argument("--env-file")
    ap.add_argument("--only", help="쉼표로 구분한 id만")
    ap.add_argument("--root", help="다른 체크아웃의 parking-chatbot 폴더로 실행(변경 전후 비교용)")
    ap.add_argument("--label", help="실행 이름(보관 파일명·장부에 씀)")
    ap.add_argument("--save", action="store_true", help="docs/eval/runs/<날짜>_<label>.json 으로 보관")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.env_file:
        from dotenv import dotenv_values
        key = dotenv_values(args.env_file).get("OPENROUTER_API_KEY")
        if key:
            os.environ.setdefault("OPENROUTER_API_KEY", key)
    log = Path(tempfile.gettempdir()) / f"dialog_turns_{os.getpid()}.jsonl"
    os.environ.update(DATA_SOURCE="mock", MONITOR_BACKEND="local", MONITOR_LOCAL_PATH=str(log),
                      LLM_ENGINE=os.environ.get("LLM_ENGINE", "langchain"), LLM_MAX_PER_SESSION_MIN="60")
    os.environ.setdefault("LLM_PROVIDER", "openrouter" if os.environ.get("OPENROUTER_API_KEY") else "none")
    root = Path(args.root).resolve() if args.root else ROOT
    question_file = Path(args.file).resolve()
    out_file = Path(args.out).resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)
    from chatbot import engine as e
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from eval_common import grade, run_meta, summarize
    meta = run_meta(str(question_file), args.label, root)
    print(f"[실행] {meta['label']} | 커밋 {meta['commit']} | 모델 {meta['model']} | 시간 제한 {meta['settings']['REQUEST_BUDGET_SEC']}초")

    spec = json.loads(question_file.read_text(encoding="utf-8"))
    only = set(args.only.split(",")) if args.only else None
    rows = []
    for dialog in spec["dialogs"]:
        if only and dialog["id"] not in only:
            continue
        sid = f"{dialog['id']}-{int(time.time() * 1000)}"
        e.handle(sid, event="start")
        for text in dialog["turns"]:
            started = time.time()
            r = e.handle(sid, text=text)
            sec = round(time.time() - started, 1)
            record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1]) if log.exists() else {}
            row = {"id": dialog["id"], "kind": dialog.get("kind"), "q": text, "a": r.get("text"),
                   "route": (r.get("meta") or {}).get("route"), "steps": record.get("route"),
                   "agent": record.get("agent"), "sec": sec, "expect": dialog.get("expect")}
            row["grade"] = grade(row)
            rows.append(row)
            print(f"{dialog['id']} | {text[:30]} | {(r.get('text') or '').replace(chr(10), ' / ')[:110]} | {row['route']} | {sec}s",
                  flush=True)
    summary = summarize(rows)
    payload = {"meta": meta, "summary": summary, "rows": rows}
    outs = [out_file]
    if args.save:
        outs.append(ROOT / "docs" / "eval" / "runs" / f"{time.strftime('%Y%m%d_%H%M')}_{meta['label']}.json")
    for out in outs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {len(rows)} turns → {' , '.join(str(o) for o in outs)}")
    print(f"판정: {summary['grades']} | AI 답 {summary['ai_answers']}/{summary['turns']} | 평균 {summary['avg_sec']}초 최대 {summary['max_sec']}초")


if __name__ == "__main__":
    main()
