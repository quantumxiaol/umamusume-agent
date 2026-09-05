import asyncio
import json
import unittest
from unittest.mock import patch

from umamusume_agent.llm_diagnostics import LLMRequestDiagnostics, llm_request_scope


class LLMRequestDiagnosticsTests(unittest.TestCase):
    def _start(self, diagnostics, messages, **kwargs):
        return diagnostics.start(
            {"model": "test", "messages": messages, "max_tokens": 6144, **kwargs},
            attempt=1, retry_reason="initial", length_retries=0,
        )

    def test_prefix_and_parameter_changes_are_logged_without_content(self):
        diagnostics = LLMRequestDiagnostics()
        messages = [{"role": "system", "content": "private system"}, {"role": "user", "content": "private user"}]
        with self.assertLogs("umamusume_agent.llm_diagnostics", level="INFO") as logs:
            with llm_request_scope(session_id="session", purpose="scene_reply", actor_id="a", turn_index=1):
                self._start(diagnostics, messages)
                self._start(diagnostics, [*messages, {"role": "assistant", "content": "private answer"}])
                self._start(diagnostics, [{"role": "system", "content": "changed"}], response_format={"type": "json_object"})
        records = [json.loads(item.getMessage().removeprefix("LLM request ")) for item in logs.records]
        self.assertIsNone(records[0]["previous_call_id"])
        self.assertTrue(records[1]["previous_messages_preserved"])
        self.assertEqual(records[1]["common_prefix_messages"], 2)
        self.assertEqual(records[1]["previous_call_id"], records[0]["call_id"])
        self.assertFalse(records[2]["previous_messages_preserved"])
        self.assertTrue(records[2]["parameters_changed"])
        self.assertNotEqual(records[2]["system_hash"], records[1]["system_hash"])
        self.assertNotIn("private", "\n".join(logs.output))

    def test_scopes_are_isolated_between_concurrent_calls(self):
        diagnostics = LLMRequestDiagnostics()

        async def task(actor_id):
            with llm_request_scope(session_id="session", purpose="scene_reply", actor_id=actor_id):
                await asyncio.sleep(0)
                self._start(diagnostics, [{"role": "user", "content": actor_id}])

        async def run():
            await asyncio.gather(task("a"), task("b"))

        with self.assertLogs("umamusume_agent.llm_diagnostics", level="INFO") as logs:
            asyncio.run(run())
            self._start(diagnostics, [{"role": "user", "content": "unscoped"}])
        records = [json.loads(item.getMessage().removeprefix("LLM request ")) for item in logs.records]
        self.assertEqual([item.get("actor_id") for item in records], ["a", "b", None])
        self.assertTrue(all(item["previous_call_id"] is None for item in records))

    def test_retained_fingerprints_are_bounded(self):
        diagnostics = LLMRequestDiagnostics(max_threads=2)
        for index in range(3):
            with llm_request_scope(session_id=str(index)):
                self._start(diagnostics, [])
        self.assertEqual(len(diagnostics._previous), 2)
        with llm_request_scope(session_id="0"):
            with self.assertLogs("umamusume_agent.llm_diagnostics", level="INFO") as logs:
                self._start(diagnostics, [])
        record = json.loads(logs.records[0].getMessage().removeprefix("LLM request "))
        self.assertIsNone(record["previous_call_id"])

    def test_diagnostic_failure_does_not_raise_or_log_content(self):
        diagnostics = LLMRequestDiagnostics()
        with patch("umamusume_agent.llm_diagnostics._digest", side_effect=ValueError("private text")):
            with self.assertLogs("umamusume_agent.llm_diagnostics", level="WARNING") as logs:
                call_id = self._start(diagnostics, [{"role": "user", "content": "private text"}])
        self.assertIsNone(call_id)
        self.assertNotIn("private text", "\n".join(logs.output))
