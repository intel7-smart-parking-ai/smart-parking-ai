"""여러 턴 대화 평가(대화 맥락·데이터 활용). LLM 없이 규칙 경로, 목업 데이터로 실행한다.

    python tests/run_dialog_eval.py                      # dev + holdout
    python tests/run_dialog_eval.py tests/dialogs/natural_dev.json -v

각 대화는 같은 세션에서 차례로 실행하며, 턴마다 기대값을 모두 만족해야 그 턴이 통과다.
모든 턴에 tests/safety_check.assert_safe도 적용한다.
기대값 키: intent(meta.intent), focus(카드의 focus 주차장), text_has / text_not(문장 포함·미포함),
chips_has(칩 ID 포함), unknown(false면 일반 실패 안내가 아니어야 함).
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("LLM_PROVIDER", "none")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chatbot import engine as e  # noqa: E402
from chatbot.adapters import CachedAdapter, MockAdapter  # noqa: E402
from safety_check import assert_safe  # noqa: E402

FALLBACK = ("잘 이해하지 못했어요", "아래에서 골라 주시거나")


def check(resp, expect):
    problems = []
    meta, text = resp.get("meta", {}), resp.get("text", "")
    chips = [c["id"] for c in resp.get("chips", [])]
    if "intent" in expect and meta.get("intent") != expect["intent"]:
        problems.append(f"intent {meta.get('intent')!r} != {expect['intent']!r}")
    if "focus" in expect:
        focus = ((resp.get("card") or {}).get("data") or {}).get("focus")
        if focus != expect["focus"]:
            problems.append(f"focus {focus!r} != {expect['focus']!r}")
    for part in expect.get("text_has", []):
        if part not in text:
            problems.append(f"text lacks {part!r}")
    for part in expect.get("text_not", []):
        if part in text:
            problems.append(f"text has {part!r}")
    for chip in expect.get("chips_has", []):
        if chip not in chips:
            problems.append(f"chips lack {chip!r}")
    if expect.get("unknown") is False and any(x in text for x in FALLBACK):
        problems.append("fallback reply")
    try:
        assert_safe(resp)  # 팀 안전 검사: 답의 'N자리' 숫자는 카드 근거에 있어야 한다
    except AssertionError as exc:
        problems.append(f"safety_check {exc}")
    return problems


def run(files, verbose=False):
    total = passed = dialogs_ok = dialogs = 0
    failures = []
    with patch.object(e, "data", CachedAdapter(MockAdapter())):
        for file in files:
            for i, dialog in enumerate(json.loads(Path(file).read_text(encoding="utf-8"))):
                e.reset_all()
                sid, ok_all = f"eval-{Path(file).stem}-{i}", True
                dialogs += 1
                for turn in dialog["turns"]:
                    resp = e.handle(sid, chip_id=turn["chip"]) if "chip" in turn else e.handle(sid, text=turn["user"])
                    problems = check(resp, turn.get("expect", {}))
                    total += 1
                    if problems:
                        ok_all = False
                        failures.append((dialog["name"], turn.get("user") or turn.get("chip"), problems, resp.get("text", "")))
                    else:
                        passed += 1
                dialogs_ok += ok_all
    print(f"턴 정답률: {passed}/{total} = {passed / total:.1%} | 대화 전체 통과: {dialogs_ok}/{dialogs}")
    for name, user, problems, text in failures:
        print(f"  실패 [{name}] {user!r}: {'; '.join(problems)}")
        if verbose:
            print("     답:", text.replace("\n", " / "))
    return passed, total


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "-v"]
    base = Path(__file__).parent / "dialogs"
    files = args or [base / "natural_dev.json", base / "natural_holdout.json"]
    for f in files:
        print(f"== {Path(f).name}")
        run([f], verbose="-v" in sys.argv)
