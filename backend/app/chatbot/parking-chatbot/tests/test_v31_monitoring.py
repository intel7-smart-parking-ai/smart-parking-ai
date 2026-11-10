"""Langfuse 모니터링(선택)이 답·흐름에 영향을 주지 않는지. 실제 Langfuse 서버나 패키지는 쓰지 않는다."""
import sys
import types
import unittest
from unittest.mock import patch

from chatbot import config, engine, monitoring
from chatbot.adapters import CachedAdapter, MockAdapter


class FakeSpan:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    def _record(self, _call, **kwargs):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.log.append((_call, kwargs))

    def update_trace(self, **kwargs):
        self._record("update_trace", **kwargs)

    def update(self, **kwargs):
        self._record("update", **kwargs)

    def score_trace(self, **kwargs):
        self._record("score_trace", **kwargs)


class FakeSpanContext:
    def __init__(self, span):
        self.span = span

    def __enter__(self):
        return self.span

    def __exit__(self, *exc):
        self.span.log.append(("end", {}))
        return False


def fake_langfuse_module(log, fail=False):
    module = types.ModuleType("langfuse")

    class Langfuse:
        def __init__(self, **kwargs):
            log.append(("init", {k: ("<set>" if "key" in k else v) for k, v in kwargs.items()}))

        def start_as_current_span(self, **kwargs):
            if fail:
                raise RuntimeError("langfuse down")
            log.append(("span", kwargs))
            return FakeSpanContext(FakeSpan(log, fail))

        def flush(self):
            log.append(("flush", {}))

    module.Langfuse = Langfuse
    return module


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        for target, attr, value in ((engine, "data", CachedAdapter(MockAdapter())), (config, "LLM_PROVIDER", "none"),
                                    (config, "DATA_SOURCE", "mock")):
            self.enterContext(patch.object(target, attr, value))
        engine.reset_all()
        monitoring.reset_for_tests()
        self.addCleanup(monitoring.reset_for_tests)
        self.addCleanup(engine.reset_all)

    def langfuse(self, log, fail=False, keys=True):
        self.enterContext(patch.dict(sys.modules, {"langfuse": fake_langfuse_module(log, fail)}))
        for attr, value in (("MONITOR_BACKEND", "langfuse"), ("LANGFUSE_PUBLIC_KEY", "pk-test" if keys else ""),
                            ("LANGFUSE_SECRET_KEY", "sk-test" if keys else "")):
            self.enterContext(patch.object(config, attr, value))

    def test_off_by_default_changes_nothing(self):
        self.assertEqual(config.MONITOR_BACKEND, "off")
        with monitoring.turn("s", text="안녕") as monitor:
            self.assertIsNone(monitor)

    def test_turn_trace_scores_and_hides_secrets(self):
        log = []
        self.langfuse(log)
        response = engine.handle("monitor-session", text="빈자리 몇 개 있어?")
        self.assertIn("자리", response["text"])
        names = [name for name, _ in log]
        self.assertIn("span", names)
        self.assertIn("update", names)
        self.assertEqual(names[-1], "end")
        trace = next(kwargs for name, kwargs in log if name == "update_trace" and "session_id" in kwargs)
        self.assertNotEqual(trace["session_id"], "monitor-session")
        self.assertNotIn("sk-test", repr(log))
        self.assertNotIn("pk-test", repr(log))
        output = next(kwargs for name, kwargs in log if name == "update")["output"]
        self.assertEqual(output["source"], "rule")
        self.assertEqual(monitoring.stats()["langfuse_sdk"], "v3")

    def test_langfuse_failure_never_breaks_answers(self):
        log = []
        self.langfuse(log, fail=True)
        for i in range(25):
            response = engine.handle("broken", text="빈자리 몇 개 있어?")
            self.assertIn("자리", response["text"])
        stats = monitoring.stats()
        self.assertGreater(stats["errors"], 0)
        self.assertEqual(stats["disabled_reason"], "too_many_errors")

    def test_missing_keys_or_package_turns_monitoring_off(self):
        log = []
        self.langfuse(log, keys=False)
        engine.handle("nokey", text="빈자리 몇 개 있어?")
        self.assertEqual(monitoring.stats()["disabled_reason"], "missing_langfuse_keys")
        monitoring.reset_for_tests()
        with patch.dict(sys.modules, {"langfuse": None}), patch.object(config, "LANGFUSE_PUBLIC_KEY", "pk"), \
                patch.object(config, "LANGFUSE_SECRET_KEY", "sk"):
            engine.handle("nopkg", text="빈자리 몇 개 있어?")
        self.assertEqual(monitoring.stats()["disabled_reason"], "langfuse_not_installed")

    def test_text_capture_can_be_disabled(self):
        log = []
        self.langfuse(log)
        with patch.object(config, "MONITOR_CAPTURE_TEXT", False):
            engine.handle("private", text="23번 차 어디 있어?")
        self.assertNotIn("23번 차 어디 있어?", repr(log))

    def test_stats_exposes_monitoring_state(self):
        self.assertIn("monitoring", engine.stats())


if __name__ == "__main__":
    unittest.main()
