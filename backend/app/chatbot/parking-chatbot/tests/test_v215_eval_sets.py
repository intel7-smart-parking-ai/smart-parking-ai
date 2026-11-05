"""Freeze the independent evaluation before prompt or hint development."""
from collections import Counter
from contextlib import redirect_stdout
import csv
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
import unittest
from unittest.mock import patch

from chatbot import config, llm_router, parser
import run_llm_compare as compare

ROOT = Path(__file__).resolve().parent
FROZEN_SHA = "102e572aa13f03e21cf42701266ee0f3205eb33d3136b4a46e6b2ace7e718215"


def normalized(text):
    return re.sub(r"[^\w가-힣]", "", unicodedata.normalize("NFKC", text).casefold())


class EvaluationSetsTests(unittest.TestCase):
    def rows(self, path):
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, ["question", "intent", "lot_id", "car_id", "zone"])
            return list(reader)

    def test_holdout_is_frozen_balanced_and_rule_unknown(self):
        path = ROOT / "llm_eval_holdout.csv"
        self.assertEqual(hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(), FROZEN_SHA)
        rows = self.rows(path)
        self.assertEqual(len(rows), 40)
        counts = Counter(row["intent"] for row in rows)
        self.assertEqual(set(counts), set(llm_router._DESCRIPTIONS))
        self.assertEqual(counts.pop("unknown"), 6)
        self.assertTrue(all(1 <= count <= 3 for count in counts.values()))
        self.assertTrue(all(parser.parse_rules(row["question"]).intent == "unknown" for row in rows))
        self.assertTrue(any(row["lot_id"] for row in rows))
        self.assertTrue(any(row["car_id"] for row in rows))

    def test_no_exact_or_near_overlap_with_existing_sets_and_training_examples(self):
        holdout = [row["question"] for row in self.rows(ROOT / "llm_eval_holdout.csv")]
        previous = []
        for path in [*ROOT.glob("questions*.csv"), ROOT / "llm_eval_questions.csv"]:
            with path.open(encoding="utf-8", newline="") as stream:
                previous.extend(row["question"] for row in csv.DictReader(stream))
        previous.extend(re.findall(r"예:\s*([^.]*)", " ".join(llm_router._DESCRIPTIONS.values())))
        try:
            from chatbot.llm_examples import EXAMPLES
        except ImportError:
            EXAMPLES = ()
        previous.extend(row[0] for row in EXAMPLES)
        self.assertEqual(len(set(map(normalized, holdout))), 40)
        scores = [difflib.SequenceMatcher(None, normalized(a), normalized(b)).ratio()
                  for a in holdout for b in previous]
        self.assertLess(max(scores), 0.8)
        self.assertNotIn("llm_eval_holdout.csv", {p.name for p in ROOT.glob("questions*.csv")})

    def test_compare_accepts_a_key_present_only_in_dotenv(self):
        with patch.dict(os.environ, {}, clear=True), patch.dict(config._dotenv, {"OPENROUTER_API_KEY":"fake-only-local-key"}), \
                patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_MODEL", "fake"), \
                patch.object(config, "LLM_ENGINE", "langchain"), patch.object(llm_router, "warmup") as warmup, \
                patch.object(llm_router, "stats", return_value={"preparing":False, "counts":{}}), \
                patch.object(parser, "parse", side_effect=parser.parse_rules), \
                patch.object(sys, "argv", ["compare", str(ROOT / "llm_eval_holdout.csv"), "--compare"]), \
                redirect_stdout(io.StringIO()) as output:
            compare.main()
        self.assertNotIn("skipped", json.loads(output.getvalue())["hybrid"])
        self.assertNotIn("fake-only-local-key", output.getvalue())
        warmup.assert_called_once()

    def test_development_examples_do_not_duplicate_the_old_evaluation(self):
        previous=[]
        for path in [*ROOT.glob("questions*.csv"), ROOT / "llm_eval_questions.csv"]:
            with path.open(encoding="utf-8", newline="") as stream:
                previous.extend(row["question"] for row in csv.DictReader(stream))
        examples=re.findall(r"예:\s*([^.]*)", " ".join(llm_router._DESCRIPTIONS.values()))
        try:
            from chatbot.llm_examples import EXAMPLES
        except ImportError:
            EXAMPLES=()
        examples.extend(row[0] for row in EXAMPLES)
        self.assertLess(max(difflib.SequenceMatcher(None,normalized(a),normalized(b)).ratio()
                            for a in examples for b in previous),0.8)

    def test_wrong_tool_distinguishes_abstention_and_argument_errors(self):
        rows = [dict(question=str(i), intent=intent, lot_id="", car_id="", zone="")
                for i, intent in enumerate(("pay_fee", "get_fee", "unknown", "get_fee"))]
        outcomes = iter([parser.ParseResult("get_fee"), parser.ParseResult("unknown"),
                         parser.ParseResult("unknown"), parser.ParseResult("get_fee", {"car_id":"23"})])
        report = compare.evaluate(rows, lambda _: next(outcomes))
        self.assertEqual(report["wrong_tool"], 1)
        self.assertEqual(report["unknown_correct"], 1)
        self.assertEqual(report["correct"], 1)


if __name__ == "__main__":
    unittest.main()
