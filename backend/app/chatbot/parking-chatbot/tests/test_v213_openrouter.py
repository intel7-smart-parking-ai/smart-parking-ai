"""v2.13: OpenRouter 제공자(DECISIONS D-18) 연결 검사. 실제 API 호출 없음."""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from chatbot import config, llm, llm_router as r  # noqa: E402

MODEL = "google/gemma-4-26b-a4b-it"


class OpenRouterSettingsTests(unittest.TestCase):
    def test_key_name_follows_team_decision(self):
        self.assertEqual(config.api_key_name("openrouter"), "OPENROUTER_API_KEY")
        self.assertEqual(config.api_key_name("openai"), "OPENAI_API_KEY")
        self.assertEqual(config.api_key_name("anthropic"), "ANTHROPIC_API_KEY")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            self.assertEqual(config.api_key("openrouter"), "or-test")

    def test_router_settings_use_openrouter_key_only(self):
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_MODEL", MODEL):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "other", "ANTHROPIC_API_KEY": "other"}):
                self.assertEqual(r._settings(), (None, "missing_key"))
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test"}):
                settings, reason = r._settings()
        self.assertIsNone(reason)
        self.assertEqual(settings[:2], ("openrouter", MODEL))
        self.assertNotIn("or-test", repr(settings))

    def test_provider_value_is_accepted_at_startup(self):
        env = {**os.environ, "LLM_PROVIDER": "openrouter", "LLM_MODEL": MODEL, "PYTHONIOENCODING": "utf-8"}
        env.pop("OPENROUTER_API_KEY", None)
        done = subprocess.run([sys.executable, "-c", "from chatbot import config; print(config.LLM_PROVIDER)"],
                              cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip().splitlines()[-1], "openrouter")


class OpenRouterDirectTests(unittest.TestCase):
    def test_direct_classifier_posts_to_openrouter_without_json_mode(self):
        sent = {}

        def fake_post(url, headers, body):
            sent.update(url=url, headers=headers, body=body)
            return {"choices": [{"message": {"content": '설명 {"intent":"get_empty_spots","params":{"lot_id":"D"}}'}}]}

        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_MODEL", MODEL), \
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test"}), patch.object(llm, "_post_json", fake_post):
            result = llm.classify("디 주차장 자리 있나 몰라")
        self.assertEqual(result, {"intent": "get_empty_spots", "params": {"lot_id": "D"}})
        self.assertEqual(sent["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(sent["headers"], {"Authorization": "Bearer or-test"})
        self.assertEqual(sent["body"]["model"], MODEL)
        self.assertNotIn("response_format", sent["body"])

    def test_openai_direct_request_is_unchanged(self):
        sent = {}

        def fake_post(url, headers, body):
            sent.update(url=url, body=body)
            return {"choices": [{"message": {"content": '{"intent":"get_empty_spots","params":{"lot_id":"A"}}'}}]}

        with patch.object(config, "LLM_PROVIDER", "openai"), patch.object(config, "LLM_MODEL", "test-model"), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "x"}), patch.object(llm, "_post_json", fake_post):
            llm.classify("에이 쪽은 어떤지 몰라")
        self.assertEqual(sent["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})


class OpenRouterModelTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("langchain_openai"), "선택 LangChain OpenAI 미설치")
    def test_langchain_model_targets_openrouter_without_network(self):
        from langchain_openai import ChatOpenAI
        clean = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        clean.update(OPENROUTER_API_KEY="or-test")
        clean.pop("OPENAI_API_KEY", None)
        clean.pop("OPENAI_BASE_URL", None)
        clean.pop("OPENAI_API_BASE", None)
        with patch.dict(os.environ, clean, clear=True):
            model = r._create_model(("openrouter", MODEL, 3, "digest"))
            self.assertIsInstance(model, ChatOpenAI)
            self.assertEqual(model.max_retries, 0)
            self.assertEqual(str(model.root_client.base_url).rstrip("/"), "https://openrouter.ai/api/v1")
            self.assertEqual(model.openai_api_key.get_secret_value(), "or-test")
            self.assertEqual(len(model.bind_tools(r.tool_schemas()).kwargs["tools"]), 22)


if __name__ == "__main__":
    unittest.main()
