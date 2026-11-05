"""Human-run model/few-shot comparison. Importing this module never calls an API."""
from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from chatbot import config, llm_router, parser
from run_llm_compare import evaluate


@contextmanager
def settings(model, fewshot):
    old_model = config.LLM_MODEL
    existed = hasattr(config, "LLM_FEWSHOT")
    old_examples = getattr(config, "LLM_FEWSHOT", False)
    config.LLM_MODEL, config.LLM_FEWSHOT = model, bool(fewshot)
    try:
        yield
    finally:
        config.LLM_MODEL = old_model
        if existed:
            config.LLM_FEWSHOT = old_examples
        else:
            del config.LLM_FEWSHOT


def prepare():
    llm_router.warmup()
    deadline = time.monotonic() + 10
    while llm_router.stats()["preparing"] and time.monotonic() < deadline:
        time.sleep(.01)
    return llm_router.stats()["ready"]


def measure(rows, model, fewshot, *, classify=None, ready=None):
    with settings(model, fewshot):
        if not (ready or prepare)():
            raise RuntimeError("모델 준비 실패: 제공자 패키지·설정을 확인해 주세요.")
        before = llm_router.stats()["counts"]
        result = evaluate(rows, classify or parser.parse)
        after = llm_router.stats()["counts"]
        for reason in ("timeout", "invalid_intent", "invalid_params", "api_error", "not_installed", "missing_key", "preparing", "busy"):
            result[reason] = after.get(reason, 0) - before.get(reason, 0)
        result["valid"] = not any(result.get(k, 0) for k in ("timeout", "api_error", "not_installed", "missing_key", "preparing", "busy"))
        return result


def summarize(model, fewshot, filename, runs):
    return {"valid":all(r.get("valid", True) for r in runs), "api_error":sum(r.get("api_error", 0) for r in runs), "model":model, "fewshot":int(fewshot), "evaluation":filename, "repeats":len(runs),
            "total":sum(r["total"] for r in runs), "correct":sum(r["correct"] for r in runs),
            "accuracy":round(sum(r["accuracy"] for r in runs) / len(runs), 4),
            **{key:sum(r[key] for r in runs) for key in
               ("wrong_tool", "unknown_correct", "unknown", "timeout", "invalid_intent", "invalid_params")},
            "mean_ms":round(sum(r["mean_ms"] for r in runs) / len(runs), 2),
            "max_ms":round(max(r["max_ms"] for r in runs), 2)}


def read_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["question", "intent", "lot_id", "car_id", "zone"]:
            raise ValueError("평가 파일의 열 구성이 맞지 않습니다.")
        rows = list(reader)
    if any(not r.get("question") or r.get("intent") not in parser.KNOWN_INTENTS
           or None in r or any(r.get(k) is None for k in ("lot_id", "car_id", "zone"))
           or (r["car_id"] and parser.normalize_car_id(r["car_id"]) is None) for r in rows):
        raise ValueError("평가 질문·정답·인자 형식이 올바르지 않습니다.")
    if not rows or any(parser.parse_rules(r["question"]).intent != "unknown" for r in rows):
        raise ValueError("비어 있거나 규칙에서 처리하는 문장이 포함된 평가 파일입니다.")
    return rows


def main(argv=None):
    args = argparse.ArgumentParser(description="실행 시 활성 제공자 API를 호출합니다. 키는 .env 또는 환경변수에서만 읽습니다.")
    args.add_argument("--models", nargs="+", required=True)
    args.add_argument("--repeats", type=int, default=3)
    args.add_argument("--files", nargs="+", default=[str(ROOT / "tests/llm_eval_questions.csv"), str(ROOT / "tests/llm_eval_holdout.csv")])
    args.add_argument("--fewshot", nargs="+", type=int, choices=(0, 1), default=[0, 1])
    args.add_argument("--output", type=Path, default=Path("llm_model_comparison.csv"))
    options = args.parse_args(argv)
    if options.repeats < 1:
        args.error("--repeats는 양수여야 합니다.")
    if config.LLM_ENGINE != "langchain" or config.LLM_PROVIDER == "none" or not config.api_key(config.LLM_PROVIDER):
        args.error("LLM_ENGINE=langchain과 제공자·키를 설정해 주세요. 키 값은 출력하지 않습니다.")
    datasets = [(Path(path).name, read_rows(path)) for path in options.files]
    # Probe a separate temporary file, never truncate an existing result before measuring.
    import tempfile
    with tempfile.TemporaryFile(dir=options.output.parent):
        pass
    summaries = []
    for model in options.models:
        for fewshot in dict.fromkeys(options.fewshot):
            for filename, rows in datasets:
                runs = [measure(rows, model, fewshot) for _ in range(options.repeats)]
                summaries.append(summarize(model, fewshot, filename, runs))
    with options.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    print("| 모델 | 예시 | 평가 파일 | 평균 정답률 | 다른 도구 | 미인식 | 시간 초과 | 평균/최대 ms |")
    print("|---|---:|---|---:|---:|---:|---:|---:|")
    for row in summaries:
        print(f"| {row['model']} | {row['fewshot']} | {row['evaluation']} | {row['accuracy']:.1%} | "
              f"{row['wrong_tool']} | {row['unknown']} | {row['timeout']} | {row['mean_ms']}/{row['max_ms']} |")
    print("세트별 unknown_correct·invalid_intent·invalid_params는 CSV에 포함합니다. 실제 정확도는 사람이 실행한 결과로 판단합니다.")
    return summaries


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
