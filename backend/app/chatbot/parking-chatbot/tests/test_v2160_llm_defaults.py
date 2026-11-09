"""D-18: OpenRouter 기본 모델(Gemma)과 키 없을 때 규칙 경로 유지. 실제 API 호출 없음."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = ("from chatbot import config, llm_router as r, parser;"
        "print(config.LLM_PROVIDER, config.LLM_MODEL, r._settings()[1], parser.parse('기차 주차장').intent)")


def run(**env):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "OPENROUTER", "ANTHROPIC", "OPENAI"))}
    done = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, env={**base, "PYTHONIOENCODING": "utf-8", **env},
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


class LLMDefaultTests(unittest.TestCase):
    def test_openrouter_without_model_uses_gemma_and_without_key_stays_on_rules(self):
        provider, model, reason, intent = run(LLM_PROVIDER="openrouter", LLM_MODEL="", OPENROUTER_API_KEY="")
        self.assertEqual((provider, model, reason, intent),
                         ("openrouter", "google/gemma-4-26b-a4b-it", "missing_key", "get_empty_spots"))

    def test_explicit_model_is_kept(self):
        self.assertEqual(run(LLM_PROVIDER="openrouter", LLM_MODEL="custom/explicit-model")[1], "custom/explicit-model")

    def test_env_example_enables_openrouter_gemma_but_never_holds_a_key(self):
        lines = [x.strip() for x in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()]
        self.assertIn("LLM_PROVIDER=openrouter", lines)
        self.assertIn("LLM_MODEL=google/gemma-4-26b-a4b-it", lines)
        self.assertIn("# OPENROUTER_API_KEY=", lines)
        self.assertFalse(any(x.startswith("OPENROUTER_API_KEY=") for x in lines))


if __name__ == "__main__":
    unittest.main()
