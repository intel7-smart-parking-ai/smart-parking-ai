"""평가 기록 도구(tools/eval_common.py, compare_runs.py)의 회귀 검사: 자동 판정·요약·비교."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from eval_common import RANK, grade, load_run, summarize  # noqa: E402
import compare_runs  # noqa: E402


def row(route, a="답", agent=None, steps=None, **extra):
    return {"id": extra.get("id", "T"), "q": extra.get("q", "질문"), "a": a, "route": route, "agent": agent or {},
            "steps": steps or [], "sec": 1.0, "kind": "k", "expect": "-"}


class GradeTests(unittest.TestCase):
    def test_each_grade(self):
        cases = {
            "ai": row("guard:pass > plan:vacancy > boundary:pass > agent:ok > agent:answer", agent={"status": "ok", "tool_calls": 1}),
            "ai_notice": row("guard:self_intro > plan:none > agent:ok > agent:answer > agent:answer_with_notice"),
            "ai_render": row("guard:pass > plan:vacancy > boundary:pass > agent:ungrounded > agent:answer",
                             steps=[{"stage": "agent", "outcome": "answer", "source": "rule_render"}]),
            "clarify_no_lookup": row("guard:pass > plan:none > boundary:pass > agent:ok > agent:answer", a="어느 주차장인가요?",
                                     agent={"status": "ok", "tool_calls": 0}),
            "rule": row("guard:emergency"),
            "fallback_timeout": row("guard:pass > plan:vacancy > boundary:pass > agent:timeout > agent:dropped > fallback:get_empty_spots"),
            "fallback_ungrounded": row("guard:pass > plan:fee > boundary:pass > agent:ungrounded > agent:dropped > fallback:get_fee",
                                       agent={"status": "ungrounded"}),
            "fallback_other": row("guard:pass > plan:vacancy > boundary:pass > agent:api_error > agent:dropped > fallback:get_empty_spots"),
            "fallback_notice": row("guard:pass > plan:none > boundary:reservation > agent:ok > agent:dropped > rule:reservation"),
            "not_understood": row("guard:pass > plan:none > boundary:pass > agent:off > fallback:unknown"),
        }
        for expected, r in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(grade(r), expected)
        for g in cases:
            self.assertIn(g, RANK)
        # 결제·승인·관리자 접수는 코드 담당(중립)
        self.assertEqual(grade(row("guard:pass > plan:payment > boundary:pass > workflow:pay_fee > rule:pay_fee")), "rule")

    def test_summary(self):
        rows = [dict(r, grade=grade(r)) for r in (row("guard:emergency"), row("guard:pass > agent:ok > agent:answer", agent={"tool_calls": 1}),
                                                   row("guard:pass > agent:timeout > fallback:get_fee"))]
        s = summarize(rows)
        self.assertEqual((s["turns"], s["ai_answers"], s["ai_ratio_excluding_rule"]), (3, 1, 0.5))


class CompareTests(unittest.TestCase):
    def test_better_worse_same(self):
        before = {"meta": {"label": "a", "commit": "1", "model": "m", "settings": {"REQUEST_BUDGET_SEC": 6}},
                  "rows": [dict(row("guard:pass > agent:timeout > fallback:get_fee", id="1", q="q1"), grade="fallback_timeout"),
                           dict(row("guard:pass > agent:ok > agent:answer", id="2", q="q2", a="같은 답"), grade="ai"),
                           dict(row("guard:pass > agent:ok > agent:answer", id="3", q="q3", a="답 A"), grade="ai")]}
        after = {"meta": {"label": "b", "commit": "2", "model": "m", "settings": {"REQUEST_BUDGET_SEC": 8}},
                 "rows": [dict(row("guard:pass > agent:ok > agent:answer", id="1", q="q1"), grade="ai"),
                          dict(row("guard:pass > agent:ok > agent:answer", id="2", q="q2", a="같은 답"), grade="ai"),
                          dict(row("guard:pass > agent:off > fallback:unknown", id="3", q="q3", a="이해 못 함"), grade="not_understood"),
                          dict(row("guard:emergency", id="4", q="q4"), grade="rule")]}
        for run in (before, after):
            run["summary"] = summarize(run["rows"])
        text, counts = compare_runs.compare(before, after)
        self.assertEqual((counts["better"], counts["worse"], counts["same"], counts["only_after"]), (1, 1, 1, 1))
        self.assertIn("**좋아짐**", text)
        self.assertIn("**나빠짐**", text)
        self.assertIn("6초 | 8초", text)

    def test_old_list_format_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "old.json"
            p.write_text(json.dumps([row("guard:pass > agent:ok > agent:answer", agent={"tool_calls": 1})]), encoding="utf-8")
            run = load_run(p)
        self.assertEqual(run["rows"][0]["grade"], "ai")
        self.assertEqual(run["summary"]["ai_answers"], 1)


if __name__ == "__main__":
    unittest.main()
