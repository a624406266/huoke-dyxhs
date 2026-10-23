"""Reflection schema regressions: synthetic responses and temporary SQLite only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from agent_harness.agents import BUSINESS_OUTPUT_CONTRACTS, DEFAULT_AGENT_SPECS
from agent_harness.feedback import FeedbackLearningService
from agent_harness.registry import PromptRegistry
from agent_harness.runtime import StructuredAgentRunner, normalize_reflection_output
from llm_client import LLMRequestError
from storage import SalesStorage


AGENT = "reflection_evolution_agent"
FIELDS = ("task_summary", "evolution_summary", "agent_summaries", "eval_cases", "prompt_improvements")


def response(**fields):
    return {"decision": "仅记录建议", "confidence": .8, "evidence": [], "risks": [], "next_actions": [],
            "task_summary": "采集不足，不能判断需求覆盖。", "evolution_summary": "先核查采集，不自动启用。",
            "agent_summaries": [], "eval_cases": [], "prompt_improvements": [], **fields}


class ReflectionContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = SalesStorage(Path(directory.name) / "synthetic.db")
        self.registry = PromptRegistry(self.storage)
        self.registry.ensure_defaults()
        self.config = {"llm": {"api_key": "synthetic-key", "model": "synthetic", "total_timeout_s": 10}}
        self.plan = {"offeringId": "general", "keywords": ["合成测试"], "searchGoal": "合成研究"}
        self.task = self.storage.create_task({"confirmedPlan": self.plan}, sales_user_id=7)
        self.storage.update_task(self.task, status="completed", result={"confirmedPlan": self.plan,
            "leads": {"leads": []}, "notes": [], "report": {"summary": "无可判断内容"}})

    def runner(self, **options):
        return StructuredAgentRunner(self.config, self.storage, self.task, registry=self.registry,
                                     run_type="evolution", **options)

    def test_active_old_prompt_always_receives_typed_contract_and_required_keys(self):
        old = {"agentKey": AGENT, "version": "reflection-evolution-agent-keyword-v3", "status": "active",
               "prompt": "旧说明：返回 task_summary、evolution_summary 等字段。"}
        self.assertTrue(set(FIELDS).issubset(DEFAULT_AGENT_SPECS[AGENT].required_keys))
        runner = self.runner()
        with patch.object(self.registry, "active_prompt", return_value=old), \
                patch("llm_client.call_llm", return_value=json.dumps(response())) as model:
            runner.run(AGENT, {"confirmedPlan": self.plan})
        self.assertEqual(model.call_count, 1)
        messages = model.call_args.args[0]
        self.assertEqual(messages[0]["content"], old["prompt"])
        self.assertIn({"role": "system", "content": BUSINESS_OUTPUT_CONTRACTS[AGENT]}, messages)
        self.assertIn("非空字符串", BUSINESS_OUTPUT_CONTRACTS[AGENT])
        self.assertIn("对象数组", BUSINESS_OUTPUT_CONTRACTS[AGENT])

    def test_object_summary_is_normalized_but_original_audit_is_preserved(self):
        original = response(task_summary={"summary": "没有足够正文。", "received": 122, "saved": 0},
                            evolution_summary={"summary": "请核查采集过滤。", "auto_enable": False})
        before = copy.deepcopy(original)
        runner = self.runner()
        with patch("llm_client.call_llm", return_value=json.dumps(original)):
            result = runner.run(AGENT, {"confirmedPlan": self.plan})
        self.assertEqual(result["task_summary"], original["task_summary"]["summary"])
        self.assertEqual(result["evolution_summary"], original["evolution_summary"]["summary"])
        audit = self.storage.get_agent_call(runner.last_call_id)
        self.assertEqual(audit["status"], "completed")
        self.assertEqual(audit["output"], before)
        self.assertEqual(normalize_reflection_output(original), result)
        self.assertEqual(original, before)

    def test_invalid_summary_contract_is_repaired_within_same_call(self):
        malformed = response(task_summary={"details": "没有 summary 字段"})
        repaired = response()
        runner = self.runner()
        with patch("llm_client.call_llm", side_effect=[json.dumps(malformed), json.dumps(repaired)]) as model:
            output = runner.run(AGENT, {"confirmedPlan": self.plan})
        self.assertEqual(model.call_count, 2)
        self.assertEqual(output, repaired)
        self.assertEqual(len(self.storage.list_agent_runs(self.task)), 1)
        self.assertEqual(self.storage.get_agent_call(runner.last_call_id)["status"], "completed")
        self.assertTrue(any("校验失败" in m["content"] for m in model.call_args.args[0]))

    def test_invalid_array_is_repaired_in_existing_json_retry_budget(self):
        runner = self.runner()
        with patch("llm_client.call_llm", side_effect=[json.dumps(response(eval_cases={})),
                                                      json.dumps(response())]) as model:
            runner.run(AGENT, {"confirmedPlan": self.plan})
        self.assertEqual(model.call_count, 2)

    def test_missing_summary_and_bad_arrays_fail_after_at_most_three_attempts(self):
        invalid = []
        for field in ("task_summary", "evolution_summary"):
            for value in (None, " ", {"summary": " "}, {"summary": 7}, {"details": "text"}, ["text"]):
                invalid.append(response(**{field: value}))
        for field in ("agent_summaries", "eval_cases", "prompt_improvements"):
            for value in (None, {}, "[]", ["unstructured"]):
                invalid.append(response(**{field: value}))
        for field in FIELDS:
            missing = response()
            del missing[field]
            invalid.append(missing)
        for index, output in enumerate(invalid):
            with self.subTest(case=index):
                runner = self.runner()
                with patch("llm_client.call_llm", return_value=json.dumps(output)) as model:
                    with self.assertRaises(LLMRequestError) as raised:
                        runner.run(AGENT, {"confirmedPlan": self.plan})
                self.assertEqual(model.call_count, 3)
                self.assertEqual(raised.exception.code, "validation_error")
                self.assertEqual(self.storage.get_agent_call(runner.last_call_id)["status"], "failed")
        self.assertTrue(all(row["error"] for row in self.storage.list_agent_runs(self.task)))

    def test_schema_retries_share_total_deadline(self):
        clock = [0.0]
        budgets = []
        self.config["llm"]["total_timeout_s"] = 1

        def fake_model(*args, **kwargs):
            budgets.append(kwargs["total_timeout_s"])
            clock[0] += .6
            return json.dumps(response(evolution_summary={}))

        runner = self.runner()
        with patch("llm_client.time", SimpleNamespace(monotonic=lambda: clock[0])), \
                patch("llm_client.call_llm", side_effect=fake_model):
            with self.assertRaises(LLMRequestError) as raised:
                runner.run(AGENT, {"confirmedPlan": self.plan})
        self.assertEqual(raised.exception.code, "total_timeout")
        self.assertEqual(len(budgets), 2)
        self.assertAlmostEqual(budgets[0], 1)
        self.assertAlmostEqual(budgets[1], .4)
        self.assertEqual(self.storage.get_agent_call(runner.last_call_id)["status"], "failed")

    def test_custom_callable_cannot_record_invalid_schema_as_success(self):
        invalid = response(task_summary={"unrelated": "text"})
        runner = self.runner(llm_json_callable=lambda *args, **kwargs: (invalid, json.dumps(invalid)))
        with self.assertRaisesRegex(ValueError, "task_summary"):
            runner.run(AGENT, {"confirmedPlan": self.plan})
        audit = self.storage.get_agent_call(runner.last_call_id)
        self.assertEqual(audit["status"], "failed")
        self.assertEqual(audit["output"], invalid)

    def test_service_saves_text_summary_without_promoting_or_rewriting_audit(self):
        original = response(task_summary={"summary": "正文为零，不推断市场没有需求。", "received": 122},
                            evolution_summary={"summary": "检查采集过滤。", "cause_layer": "collection"})
        runner = self.runner()
        active_before = {row["id"] for row in self.registry.prompt_versions() if row["status"] == "active"}
        service = FeedbackLearningService(self.config, self.storage)
        with patch("llm_client.call_llm", return_value=json.dumps(original)) as model:
            evolution = service.reflect_task(self.task, runner=runner)
            same = service.reflect_task(self.task, runner=runner)
        self.assertEqual(evolution, same)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(evolution["status"], "needs_review")
        self.assertEqual(evolution["candidatePromptIds"], [])
        self.assertEqual(evolution["summary"], original["evolution_summary"]["summary"])
        saved = self.storage.get_task_summary(self.task)
        self.assertEqual(saved["reflectionAnalysis"]["task_summary"], original["task_summary"]["summary"])
        self.assertEqual(self.storage.get_agent_call(runner.last_call_id)["output"], original)
        self.assertEqual(active_before, {row["id"] for row in self.registry.prompt_versions() if row["status"] == "active"})

    def test_service_custom_runner_reuses_strict_normalizer(self):
        invalid = response(evolution_summary={"summary": ["cannot stringify"]})
        fake = SimpleNamespace(run=lambda *args, **kwargs: copy.deepcopy(invalid))
        service = FeedbackLearningService(self.config, self.storage)
        result = service.reflect_task(self.task, runner=fake)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["candidatePromptIds"])
        self.assertIn("evolution_summary", result["error"])
        self.assertNotIn("reflectionAnalysis", self.storage.get_task_summary(self.task))


if __name__ == "__main__":
    unittest.main()
