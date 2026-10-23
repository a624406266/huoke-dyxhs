from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_harness.registry import PromptRegistry
from agent_harness.runtime import StructuredAgentRunner, model_event_logger
from llm_client import _record_usage


class MemoryStorage:
    def __init__(self):
        self.events = []
        self.runs = []
        self.audits = {}

    def start_agent_call(self, **kwargs):
        self.audits[kwargs["call_id"]] = {"status": "running"}

    def finish_agent_call(self, call_id, **kwargs):
        self.audits[call_id].update(kwargs)

    def add_task_event(self, task_id, level, title, message, details):
        self.events.append({
            "taskId": task_id, "level": level, "title": title,
            "message": message, "details": details,
        })

    def save_agent_run(self, **kwargs):
        self.runs.append(kwargs)


class RuntimeModelProgressTests(unittest.TestCase):
    @staticmethod
    def valid_result():
        return {"decision": "complete", "confidence": 0.8, "evidence": [],
                "risks": [], "next_actions": [], "notes": []}

    def test_runner_passes_configured_budget_and_callback_to_injected_callable(self):
        calls = []
        storage = MemoryStorage()

        def fake_llm(messages, **kwargs):
            calls.append(kwargs)
            kwargs["event_callback"]({"phase": "connecting", "attempt": 1, "elapsedSeconds": 0})
            result = self.valid_result()
            return result, json.dumps(result)

        options = {"connect_timeout_s": 17, "read_timeout_s": 48, "total_timeout_s": 220}
        runner = StructuredAgentRunner(
            {"llm": {"api_key": "fixture-key", "model": "fixture", **options}},
            storage, 2, registry=PromptRegistry(), llm_json_callable=fake_llm, run_id="run-fixture",
        )
        runner.run("relevance_agent", {"notes": []})
        self.assertEqual(len(calls), 1)
        for key, expected in options.items():
            self.assertEqual(calls[0][key], expected)
        self.assertEqual(calls[0]["retries"], 2)
        self.assertEqual(storage.events[0]["details"]["agentKey"], "relevance_agent")
        self.assertEqual(storage.events[0]["details"]["runId"], "run-fixture")

    def test_runner_uses_default_request_budgets(self):
        calls = []

        def fake_llm(messages, **kwargs):
            calls.append(kwargs)
            result = self.valid_result()
            return result, json.dumps(result)

        StructuredAgentRunner(
            {"llm": {"model": "fixture"}}, registry=PromptRegistry(), llm_json_callable=fake_llm,
        ).run("relevance_agent", {})
        self.assertEqual(
            {key: calls[0][key] for key in ("connect_timeout_s", "read_timeout_s", "total_timeout_s")},
            {"connect_timeout_s": 30, "read_timeout_s": 60, "total_timeout_s": 300},
        )

    def test_usage_stays_in_audit_without_notifications_or_invented_zero(self):
        cases = [
            ([None], (None, None, None)),
            ([{"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}], (0, 0, 0)),
            ([{"prompt_tokens": 0}], (0, None, None)),
            ([{"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}, None], (3, 4, 7)),
            ([{"prompt_tokens": 3}, {"completion_tokens": 4, "total_tokens": 7}], (3, 4, 7)),
        ]
        for records, expected_values in cases:
            with self.subTest(records=records):
                storage = MemoryStorage()

                def fake_llm(messages, **kwargs):
                    for usage in records:
                        _record_usage(usage)
                    result = self.valid_result()
                    return result, json.dumps(result)

                runner = StructuredAgentRunner({"llm": {"model": "fixture"}}, storage, 2,
                    registry=PromptRegistry(), llm_json_callable=fake_llm)
                runner.run("relevance_agent", {})
                self.assertFalse(any(item["title"] == "Model Usage" for item in storage.events))
                audit = storage.audits[runner.last_call_id]
                self.assertEqual(audit["status"], "completed")
                usage = audit["metrics"]["usage"]
                self.assertEqual(tuple(usage[key] for key in ("inputTokens", "outputTokens", "totalTokens")), expected_values)
                self.assertEqual(storage.runs[0]["error"], "")

    def test_usage_presence_does_not_change_failed_call_or_emit_notifications(self):
        for provider_usage in (None, {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}):
            with self.subTest(provider_usage=provider_usage):
                storage = MemoryStorage()

                def fake_llm(messages, **kwargs):
                    _record_usage(provider_usage)
                    raise RuntimeError("合成调用失败")

                runner = StructuredAgentRunner({"llm": {"model": "fixture"}}, storage, 2,
                    registry=PromptRegistry(), llm_json_callable=fake_llm)
                with self.assertRaisesRegex(RuntimeError, "合成调用失败"):
                    runner.run("relevance_agent", {})
                self.assertFalse(any(item["title"] == "Model Usage" for item in storage.events))
                self.assertEqual(storage.runs[0]["error"], "合成调用失败")
                audit = storage.audits[runner.last_call_id]
                self.assertEqual(audit["status"], "failed")
                self.assertEqual(audit["metrics"]["usage"]["totalTokens"], 5 if provider_usage else None)

    def test_stream_progress_is_throttled_and_new_attempt_remains_visible(self):
        storage = MemoryStorage()
        callback = model_event_logger(storage, 2, "relevance_agent")
        callback({"phase": "connecting", "attempt": 1, "elapsedSeconds": 0})
        with patch("agent_harness.runtime.time.monotonic", side_effect=[100, 114.9, 115, 116, 116]):
            for elapsed in (1, 14.9, 16, 17):
                callback({"phase": "streaming", "attempt": 1, "elapsedSeconds": elapsed,
                          "receivedCharacters": 3, "reasoningCharacters": 20})
            callback({"phase": "retry", "attempt": 1, "code": "stream_idle_timeout"})
            callback({"phase": "connecting", "attempt": 2, "elapsedSeconds": 18})
            callback({"phase": "streaming", "attempt": 2, "elapsedSeconds": 19})
        callback({"phase": "complete", "attempt": 2, "elapsedSeconds": 20})
        phases = [event["details"]["phase"] for event in storage.events]
        self.assertEqual(phases, ["connecting", "streaming", "streaming", "retry",
                                  "connecting", "streaming", "complete"])
        self.assertEqual(storage.events[3]["details"]["code"], "stream_idle_timeout")

    def test_events_only_store_numeric_progress_and_known_diagnostic_codes(self):
        storage = MemoryStorage()
        callback = model_event_logger(storage, 2, "relevance_agent")
        secret = "fixture-sensitive-value"
        callback({"phase": "error", "attempt": 1, "elapsedSeconds": 45.123456,
                  "receivedCharacters": 12, "reasoningCharacters": 123,
                  "code": "first_response_timeout", "error_type": "ReadTimeout",
                  "reasoning_content": secret, "content": secret, "api_key": secret,
                  "message": secret, "headers": {"Authorization": secret}})
        callback({"phase": "error", "attempt": 2, "elapsedSeconds": float("nan"),
                  "receivedCharacters": -1, "reasoningCharacters": secret,
                  "code": secret, "error_type": secret})
        callback({"phase": "error", "attempt": 3, "code": {"secret": secret},
                  "error_type": [secret]})
        serialized = json.dumps(storage.events, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("reasoning_content", serialized)
        self.assertEqual(storage.events[0]["details"], {
            "runId": "", "agentKey": "relevance_agent", "phase": "error", "attempt": 1,
            "elapsedSeconds": 45.123, "receivedCharacters": 12, "reasoningCharacters": 123,
            "code": "first_response_timeout", "error_type": "ReadTimeout",
        })
        self.assertEqual(storage.events[1]["details"], {
            "runId": "", "agentKey": "relevance_agent", "phase": "error", "attempt": 2,
        })

    def test_progress_storage_failure_does_not_fail_model_request(self):
        class FailingEvents(MemoryStorage):
            def add_task_event(self, *args):
                raise RuntimeError("fixture logging failure")

        callback = model_event_logger(FailingEvents(), 2, "relevance_agent")
        callback({"phase": "connecting", "attempt": 1})
        callback({"phase": "streaming", "attempt": 1})
        callback({"phase": "complete", "attempt": 1})

    def test_failed_run_redacts_configured_key_and_retains_safe_error_code(self):
        storage = MemoryStorage()
        secret = "fixture-private-key-without-standard-prefix"

        def fake_llm(messages, **kwargs):
            kwargs["event_callback"]({"phase": "error", "attempt": 1, "code": "dns_error",
                                      "error_type": "ConnectError", "reasoning_content": secret})
            raise RuntimeError("fixture failed with " + secret)

        runner = StructuredAgentRunner(
            {"llm": {"api_key": secret, "model": "fixture"}},
            storage, 2, registry=PromptRegistry(), llm_json_callable=fake_llm,
        )
        with self.assertRaises(RuntimeError):
            runner.run("relevance_agent", {})
        self.assertNotIn(secret, json.dumps({"events": storage.events, "runs": storage.runs}))
        self.assertEqual(storage.events[0]["details"]["code"], "dns_error")
        self.assertIn("[凭据已脱敏]", storage.runs[0]["error"])



if __name__ == "__main__":
    unittest.main()
