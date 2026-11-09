"""Exercise the real LangChain graph locally with an observable chat model."""
import asyncio
import importlib.util
import json
import os
import unittest
from unittest.mock import patch

from chatbot.agents.conversation import AgentOptions, arun_conversation
from chatbot import config

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None
if HAS_LANGCHAIN:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import StructuredTool
    from pydantic import PrivateAttr

    class ScriptedModel(BaseChatModel):
        _answers = PrivateAttr(default_factory=list)
        _received = PrivateAttr(default_factory=list)

        def __init__(self, answers):
            super().__init__()
            self._answers = list(answers)

        @property
        def _llm_type(self):
            return "parking-test"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError("async path expected")

        async def _agenerate(self, messages, **kwargs):
            self._received.append(messages)
            answer = self._answers.pop(0)
            if callable(answer):
                answer = answer(messages)
            if asyncio.iscoroutine(answer):
                answer = await answer
            return ChatResult(generations=[ChatGeneration(message=answer)])

    def tool_call(name, args=None):
        return AIMessage(content="", tool_calls=[{"name": name, "id": "read1", "args": args or {}}])

    async def parking_status() -> dict:
        """Return the scoped parking status in the model test."""
        return {"ok": True, "data": {"rows": [{"lot_id": "C", "name": "김해공항 주차장", "empty": 5}]}}

    def repeat_observation(messages):
        observation = next(m for m in messages if isinstance(m, ToolMessage))
        count = json.loads(observation.content)["data"]["rows"][0]["empty"]
        return AIMessage(content=f"김해공항 주차장의 현재 빈자리는 {count}자리예요.")

@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class AgentGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tools = [StructuredTool.from_function(coroutine=parking_status)]

    async def run_turn(self, model, **kwargs):
        return await arun_conversation("공항에 몇 자리 있어?", [], {}, self.tools, model=model, **kwargs)

    async def test_model_receives_real_tool_result(self):
        model = ScriptedModel([tool_call("parking_status"), repeat_observation])
        result = await self.run_turn(model)
        self.assertEqual((result.status, result.metadata["model_calls"], result.metadata["tool_calls"]), ("ok", 2, 1))
        self.assertIn("5자리", result.text)

    async def test_requires_observation(self):
        result = await self.run_turn(ScriptedModel([AIMessage(content="공항에 4자리가 있어요.")]))
        self.assertEqual((result.status, result.text), ("ungrounded", ""))

    async def test_rejects_unobserved_value_and_action(self):
        for answer, reason in [("공항에 42자리예요.", "unsupported_number"),
                               ("모의 결제를 완료했습니다.", "unsupported_action")]:
            with self.subTest(answer=answer):
                model = ScriptedModel([tool_call("parking_status"), AIMessage(content=answer)])
                result = await self.run_turn(model)
                self.assertEqual(result.status, "ungrounded")
                self.assertEqual(result.metadata["grounding_error"], reason)
                self.assertEqual(result.proposals, [])
                self.assertTrue(result.tool_results)

    async def test_failed_tool_is_not_grounding(self):
        async def fee_policies():
            """Read unavailable fee policies."""
            return {"ok": False, "error": "조회 실패", "data": {}}
        self.tools = [StructuredTool.from_function(coroutine=fee_policies)]
        result = await self.run_turn(ScriptedModel([tool_call("fee_policies"), AIMessage(content="무료입니다.")]))
        self.assertEqual(result.status, "ungrounded")

    async def test_clarification_uses_controlled_question(self):
        result = await self.run_turn(ScriptedModel([tool_call("ask_clarification", {"topic": "vehicle"}),
                                                     AIMessage(content="23번 차량을 결제했습니다.")]))
        self.assertEqual(result.status, "clarify")
        self.assertEqual(result.text, "어느 차량인지 번호를 알려주시겠어요?")

    async def test_model_bound_preserves_observation(self):
        result = await self.run_turn(ScriptedModel([tool_call("parking_status")]), options=AgentOptions(max_model_calls=1))
        self.assertEqual(result.status, "model_limit")
        self.assertEqual(len(result.tool_results), 1)

    async def test_timeout_cancels_model_and_releases_slot(self):
        cancelled = asyncio.Event()
        async def slow(_messages):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()
        with patch.object(config, "LLM_TIMEOUT_SEC", .03):
            result = await self.run_turn(ScriptedModel([slow]))
        self.assertEqual(result.status, "timeout")
        self.assertTrue(cancelled.is_set())

@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class ProviderWireTests(unittest.TestCase):
    def test_openrouter_adapter_roundtrip_on_localhost(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from chatbot.agents.conversation import run_conversation
        calls = []
        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, req))
                tool_rows = [m for m in req["messages"] if m["role"] == "tool"]
                if tool_rows:
                    count = json.loads(tool_rows[-1]["content"])["data"]["rows"][0]["empty"]
                    message, reason = {"role": "assistant", "content": f"공항에 현재 {count}자리예요."}, "stop"
                else:
                    message = {"role": "assistant", "content": None,
                               "tool_calls": [{"type": "function", "id": "local1",
                                               "function": {"name": "parking_status", "arguments": "{}"}}]}
                    reason = "tool_calls"
                body = json.dumps({"id": "local", "object": "chat.completion", "created": 0,
                                   "model": req["model"], "choices": [
                                       {"index": 0, "message": message, "finish_reason": reason}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        # A loopback provider must bypass any host-configured HTTP or SOCKS proxy.
        environment = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_MODEL", "local-test"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(config, "OPENROUTER_BASE_URL", f"http://127.0.0.1:{server.server_port}/api/v1"):
            for _ in range(2):
                result = run_conversation("공항은 지금?", [], {}, [StructuredTool.from_function(coroutine=parking_status)])
                self.assertEqual(result.status, "ok", result)
                self.assertIn("5자리", result.text)
        self.assertEqual([path for path, _ in calls], ["/api/v1/chat/completions"] * 4)
        self.assertTrue(all(req["model"] == "local-test" for _, req in calls))
