"""Exercise the human measurement runner with in-memory fake classifications."""
from contextlib import redirect_stdout
import csv
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chatbot import config, parser, llm_router

SPEC = importlib.util.spec_from_file_location("compare_models_for_test", Path(__file__).resolve().parents[1] / "tools/compare_models.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ModelComparisonTests(unittest.TestCase):
    def rows(self):
        return [dict(question="합성 평가 문장", intent="get_fee", lot_id="", car_id="", zone="")]

    def test_fake_run_restores_settings_and_reports_metrics(self):
        original = config.LLM_MODEL
        with patch.object(llm_router, "stats", return_value={"counts":{}}):
            report = runner.measure(self.rows(), "fake", 1, ready=lambda:True,
                                    classify=lambda _:parser.ParseResult("get_fee"))
        self.assertEqual(config.LLM_MODEL, original)
        self.assertEqual(report["correct"], 1)
        self.assertEqual(report["wrong_tool"], 0)
        self.assertEqual(report["timeout"], 0)

    def test_fake_cli_compares_each_model_mode_file_and_repeat_without_leaking_key(self):
        row = dict(correct=1,total=1,accuracy=1.0,mean_ms=12,max_ms=20,
                   wrong_tool=0,unknown_correct=0,unknown=0,timeout=0,invalid_intent=0,invalid_params=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(config,"LLM_PROVIDER","openrouter"), \
                patch.object(config,"LLM_ENGINE","langchain"), patch.object(config,"api_key",return_value="fake-private-key"), \
                patch.object(runner,"read_rows",return_value=self.rows()), patch.object(runner,"measure",return_value=row) as run, \
                redirect_stdout(io.StringIO()) as output:
            target=Path(directory)/"results.csv"
            result=runner.main(["--models","fake-a","fake-b","--fewshot","0","1","--repeats","3", "--files","a.csv","b.csv","--output",str(target)])
            text=target.read_text(encoding="utf-8")
        self.assertEqual(run.call_count, 24)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[0]["total"], 3)
        self.assertNotIn("fake-private-key", text + output.getvalue())

    def test_failure_restores_model_and_fewshot(self):
        previous = (config.LLM_MODEL, getattr(config,"LLM_FEWSHOT",False), hasattr(config,"LLM_FEWSHOT"))
        with self.assertRaises(RuntimeError):
            runner.measure(self.rows(),"broken",1,ready=lambda:False)
        self.assertEqual((config.LLM_MODEL,getattr(config,"LLM_FEWSHOT",False),hasattr(config,"LLM_FEWSHOT")),previous)


if __name__ == "__main__":
    unittest.main()
