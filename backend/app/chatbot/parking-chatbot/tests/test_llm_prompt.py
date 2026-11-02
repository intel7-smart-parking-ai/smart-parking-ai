"""LLM 분류 지시문이 주차장 명부를 따라가는지 검사 (LLM 실호출 없음)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chatbot import config, llm  # noqa: E402


class LlmPromptTest(unittest.TestCase):
    def tearDown(self):
        config.catalog.reset()

    def test_prompt_lists_every_lot_in_settings(self):
        prompt = llm.system_prompt()
        ids = config.catalog.ids()
        self.assertIn("lot_id(" + "/".join(ids) + ")", prompt)
        self.assertIn("D", ids)
        self.assertIn("D(D 주차장·디)", prompt)
        self.assertIn(f"vehicle_no({config.VEHICLE_MIN:02d}~{config.VEHICLE_MAX:02d}", prompt)

    def test_prompt_follows_backend_catalog(self):
        config.catalog.adopt({"A": {"name": "A 주차장", "total": 8}, "E": {"name": "E 주차장", "kind": "virtual", "total": 6}})
        prompt = llm.system_prompt()
        self.assertIn("lot_id(A/E)", prompt)
        self.assertNotIn("lot_id(A/B", prompt)

    def test_classify_sends_current_prompt(self):
        sent = {}

        def fake_post(url, headers, body):
            sent.update(body)
            return {"content": [{"text": '{"intent":"get_empty_spots","params":{"lot_id":"D"}}'}]}

        with patch.object(config, "LLM_PROVIDER", "anthropic"), patch.object(config, "LLM_MODEL", "test-model"), \
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}), patch.object(llm, "_post_json", fake_post):
            result = llm.classify("디 쪽 남았어?")
        self.assertEqual(result["params"]["lot_id"], "D")
        self.assertIn("A/B/C/D", sent["system"])


if __name__ == "__main__":
    unittest.main()
