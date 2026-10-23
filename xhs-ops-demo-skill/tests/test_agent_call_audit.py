"""Complete call evidence and decision transitions, using synthetic temporary DBs."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from agent_harness.registry import PromptRegistry
from agent_harness.runtime import StructuredAgentRunner, redact_external
from storage import SalesStorage


def result(**fields):
    return {"decision": "complete", "confidence": 0.8, "evidence": [], "risks": [],
            "next_actions": [], **fields}


class AgentCallAuditTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fixture.db"
        self.storage = SalesStorage(self.path)
        self.task = self.storage.create_task({"keyword": "fixture"}, sales_user_id=7)

    def runner(self, fake, *, secret="synthetic-key"):
        return StructuredAgentRunner({"llm": {"model": "fixture", "api_key": secret}}, self.storage,
            self.task, registry=PromptRegistry(), llm_json_callable=fake, run_id="synthetic-run")

    def test_complete_redacted_evidence_survives_summary_truncation_and_links_events(self):
        source = "完整证据，保留每个字符。" * 700
        private = "synthetic-identity"
        secret = "configured-secret-without-prefix"
        payload = {"notes": [{"note_id": "note-1", "content": source, "user_id": private,
                              "cookie": private, "reasoning_content": private}], "credentialEcho": secret}
        captured = []

        def fake(messages, **kwargs):
            call = self.storage.get_agent_call(runner.last_call_id, sales_user_id=7)
            self.assertEqual(call["status"], "running")
            self.assertIsNone(call["output"])
            self.assertEqual(call["request"]["messages"], messages)
            captured.append(json.loads(messages[-1]["content"]))
            kwargs["event_callback"]({"phase": "connecting", "attempt": 1})
            output = result(notes=[{"note_id": "note-1", "reason": source}], api_key=secret,
                            credentialEcho=secret, reasoning_content=private)
            return output, json.dumps(output)

        runner = self.runner(fake, secret=secret)
        output = runner.run("relevance_agent", payload)
        first_id = runner.last_call_id
        runner.run("relevance_agent", payload)
        self.assertNotEqual(first_id, runner.last_call_id)
        run = self.storage.list_agent_runs(self.task)[0]
        self.assertEqual(run["callId"], first_id)
        self.assertTrue(run["inputSummaryTruncated"])
        self.assertTrue(run["outputSummaryTruncated"])
        self.assertTrue(run["hasFullAudit"])
        self.assertNotIn("input", run)
        call = self.storage.get_agent_call(first_id, task_id=self.task, sales_user_id=7)
        self.assertEqual(call["input"]["notes"][0]["content"], source)
        self.assertEqual(call["output"]["notes"][0]["reason"], source)
        self.assertEqual(json.loads(call["request"]["messages"][-1]["content"]), captured[0])
        self.assertEqual(call["metrics"]["coveredCount"], 1)
        self.assertEqual(call["metrics"]["missingCount"], 0)
        self.assertEqual(call["metrics"]["callIndex"], 1)
        self.assertEqual(self.storage.get_agent_call(runner.last_call_id)["metrics"]["agentCallIndex"], 2)
        with self.storage.connect() as db:
            events = [json.loads(row[0]) for row in db.execute("SELECT data FROM task_events WHERE task_id=?", (self.task,))]
        self.assertEqual([event["callId"] for event in events], [first_id, runner.last_call_id])
        serialized = json.dumps({"runs": self.storage.list_agent_runs(self.task), "call": call,
                                 "events": events, "sent": captured}, ensure_ascii=False)
        self.assertEqual(call["input"]["notes"][0]["user_id"], private)
        self.assertEqual(call["input"]["notes"][0]["cookie"], "[已脱敏]")
        self.assertEqual(call["input"]["notes"][0]["reasoning_content"], "[已脱敏]")
        self.assertNotIn(secret, serialized)
        # Do not mutate the model's object: the pipeline still needs to preserve the raw decision.
        self.assertEqual(output["credentialEcho"], secret)
        self.assertIsNone(self.storage.get_agent_call(first_id, sales_user_id=8))
        self.assertIsNone(self.storage.get_agent_call(first_id, task_id=self.task + 1))

    def test_duplicate_missing_and_unknown_ids_are_distinct_from_transport_attempts(self):
        payload = {"leadCandidates": [{"candidate_id": f"candidate-{i}", "content": "完整证据"} for i in range(5)]}
        output = result(leads=[{"candidate_id": f"candidate-{i}"} for i in (0, 1, 1, 3, 99)])
        usage = {"source": "provider", "requestCount": 1, "inputTokens": 100, "outputTokens": 50,
                 "totalTokens": 150, "available": True, "complete": True, "requests": []}
        runner = self.runner(lambda *args, **kwargs: (output, json.dumps(output)))
        with patch("agent_harness.runtime.get_last_llm_usage", return_value=usage):
            runner.run("lead_signal_agent", payload)
        metrics = self.storage.get_agent_call(runner.last_call_id)["metrics"]
        self.assertEqual({key: metrics[key] for key in ("inputCount", "outputCount", "coveredCount", "missingCount", "duplicateCount", "unknownCount")},
                         {"inputCount": 5, "outputCount": 5, "coveredCount": 3, "missingCount": 2, "duplicateCount": 1, "unknownCount": 1})
        self.assertEqual(metrics["usage"]["requestCount"], 1)
        self.assertEqual(metrics["usage"]["totalTokens"], 150)

    def test_qa_per_item_coverage_does_not_invent_missing_review_success(self):
        output = result(approved=True, issues=[], review_requests=[],
                        reviews=[{"target_id": "lead-1", "status": "approved", "reason": "判断与原文一致"}])
        captured = []

        def fake(messages, **kwargs):
            captured.extend(messages)
            return output, json.dumps(output)

        runner = self.runner(fake)
        runner.run("critic_qa_agent", {"leads": [{"lead_id": "lead-1"}, {"lead_id": "lead-2"}]})
        metrics = self.storage.get_agent_call(runner.last_call_id)["metrics"]
        self.assertEqual((metrics["coveredCount"], metrics["missingCount"]), (1, 1))
        self.assertEqual(metrics["coverageBasis"], "returned_ids")
        self.assertIn("逐条返回 reviews", "\n".join(message["content"] for message in captured))

    def test_failed_call_records_complete_input_and_safe_error_without_success_coverage(self):
        secret = "configured-failure-secret"

        def fake(messages, **kwargs):
            kwargs["event_callback"]({"phase": "error", "attempt": 1, "code": "total_timeout"})
            raise RuntimeError("synthetic failure " + secret)

        runner = self.runner(fake, secret=secret)
        with self.assertRaises(RuntimeError):
            runner.run("relevance_agent", {"notes": [{"note_id": "note-1", "content": "evidence" * 900}]})
        call = self.storage.get_agent_call(runner.last_call_id)
        self.assertEqual(call["status"], "failed")
        self.assertEqual(call["metrics"]["coveredCount"], 0)
        self.assertEqual(call["metrics"]["missingCount"], 1)
        self.assertNotIn(secret, json.dumps(call))
        self.assertIn("[凭据已脱敏]", call["error"])
        with self.assertRaises(ValueError):
            self.storage.finish_agent_call(runner.last_call_id, status="completed", output={}, metrics={})

    def test_invalid_structured_output_is_audited_without_masking_validation_error(self):
        runner = self.runner(lambda *args, **kwargs: (["invalid-object"], "raw"))
        with self.assertRaisesRegex(ValueError, "不是 JSON 对象"):
            runner.run("relevance_agent", {"notes": []})
        call = self.storage.get_agent_call(runner.last_call_id)
        self.assertEqual(call["status"], "failed")
        self.assertEqual(call["output"], ["invalid-object"])

    def test_raw_and_effective_decisions_remain_separate_and_untruncated(self):
        original = {"classification": "demand", "evidence": ["原文证据" * 1600], "user_id": "synthetic-private"}
        effective = {"classification": "uncertain", "reason": "缺少委托证据"}
        self.storage.save_audit_decision(self.task, stage="lead_signal", entity_type="comment", entity_id="candidate-1",
            decision="below_threshold", call_id="synthetic-call", original_decision=original,
            final_decision=effective, rule_version="fixture-v2", transformation_reason="原文不足", source_keyword="智能体找人开发")
        record = self.storage.list_audit_decisions(self.task, sales_user_id=7)["items"][0]
        self.assertEqual(record["originalDecision"]["evidence"], original["evidence"])
        self.assertEqual(record["originalDecision"]["classification"], "demand")
        self.assertEqual(record["finalDecision"]["classification"], "uncertain")
        self.assertEqual(record["callId"], "synthetic-call")
        self.assertEqual(record["ruleVersion"], "fixture-v2")
        self.assertEqual(record["sourceKeyword"], "智能体找人开发")
        self.assertEqual(record["originalDecision"]["user_id"], original["user_id"])
        self.assertEqual(self.storage.list_audit_decisions(self.task, sales_user_id=8)["items"], [])

    def test_reopening_current_schema_preserves_complete_audit_records(self):
        self.storage.start_agent_call(call_id="reopen-fixture", task_id=self.task, agent_key="fixture",
            input_payload={"source": "synthetic"}, request={"messages": []}, metrics={}, started_at=123)
        self.storage.finish_agent_call("reopen-fixture", status="completed", output={"ok": True}, metrics={"outputCount": 1})
        before = self.storage.get_agent_call("reopen-fixture")
        self.assertIsNotNone(before)
        for _ in range(2):
            reopened = SalesStorage(self.path)
            self.assertEqual(reopened.get_agent_call("reopen-fixture"), before)

    def test_scoped_candidate_is_resolved_before_request_and_trace_version_are_written(self):
        registry = PromptRegistry(self.storage)
        registry.ensure_defaults()
        base = registry.active_prompt("relevance_agent")
        candidate_id = self.storage.create_prompt_candidate(agent_key="relevance_agent", agent_name="fixture",
            base_version=base["version"], prompt="scoped synthetic instruction", source_task_id=self.task, notes="fixture")
        self.storage.update_prompt_score(candidate_id, 0, {"learningContext": {"offeringId": "agent_custom_development"},
                                                          "basePromptId": base["id"]})
        self.storage.promote_prompt_version(candidate_id)  # Synthetic activated fixture only.
        requests = []
        def fake(messages, **kwargs):
            requests.append(messages)
            return result(notes=[]), "{}"
        runner = StructuredAgentRunner({"llm": {"model": "fixture"}}, self.storage, self.task,
                                       registry=registry, llm_json_callable=fake)
        runner.run("relevance_agent", {"plan": {"offeringId": "general"}, "notes": []})
        general = self.storage.list_agent_runs(self.task)[0]
        runner.run("relevance_agent", {"confirmedPlan": {"offeringId": "agent_custom_development"}, "notes": []})
        self.assertEqual(requests[0][0]["content"], base["prompt"])
        self.assertIn(base["version"], general["promptVersion"])
        self.assertEqual(requests[1][0]["content"], "scoped synthetic instruction")


if __name__ == "__main__":
    unittest.main()
