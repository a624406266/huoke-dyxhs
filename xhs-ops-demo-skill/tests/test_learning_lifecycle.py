"""Learning lifecycle regressions. Temp SQLite + synthetic runners only; no network."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from agent_harness.evaluator import HarnessEvaluator
from agent_harness.feedback import FeedbackLearningService
from agent_harness.offering import AGENT_CUSTOM_DEVELOPMENT, SERVICE_POLICY_VERSION
from agent_harness.registry import PromptRegistry
from storage import SalesStorage
from workbench_store import WorkbenchStore


PLAN = {"offeringId": AGENT_CUSTOM_DEVELOPMENT, "searchGoal": "寻找本人委托开发，排除教学",
        "keywords": ["合成委托测试"], "excludeKeywords": ["合成排除"], "platforms": ["xhs"],
        "servicePolicyVersion": SERVICE_POLICY_VERSION, "minScore": 60}
SOURCE = "我们需要门店客服智能体，请人开发，能报价吗？"
LEAD = {"lead_id": "note-1:comment-1", "comment_id": "comment-1", "note_id": "note-1",
        "platform": "xhs", "content": SOURCE, "evidence": SOURCE, "classification": "uncertain",
        "qualified": False, "qa_status": "approved", "score": 40,
        "source_comment": {"candidate_id": "candidate-1"}}
COMMON = {"decision": "合成测试", "confidence": .8, "evidence": [], "risks": [], "next_actions": []}


def improvement(agent="lead_signal_agent", **extra):
    return {"agent_key": agent, "title": "核对委托归属", "prompt_patch": "结合本人原文核对委托归属。",
            "cause_layer": "prompt", "evidence_ids": [LEAD["lead_id"]], **extra}


def reflection(*items):
    return {**COMMON, "task_summary": "合成任务复盘", "agent_summaries": [], "eval_cases": [],
            "prompt_improvements": list(items), "evolution_summary": "仅生成待验收建议。"}


class Runner:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def run(self, key, data, purpose=""):
        self.calls.append((key, copy.deepcopy(data)))
        if isinstance(self.output, Exception):
            raise self.output
        return copy.deepcopy(self.output)


class LearningLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = SalesStorage(Path(self.tmp.name) / "fixture.db")
        self.store = WorkbenchStore(self.storage)
        self.registry = PromptRegistry(self.storage)
        self.registry.ensure_defaults()
        self.service = FeedbackLearningService({"llm": {"api_key": "fixture-key"}}, self.storage)
        self.task = self.storage.create_task({"confirmedPlan": copy.deepcopy(PLAN)}, sales_user_id=7)
        self.result = {"confirmedPlan": copy.deepcopy(PLAN), "leads": {"leads": [copy.deepcopy(LEAD)]},
                       "qa": {"approved": True, "issues": [], "review_requests": [],
                              "reviews": [{"target_id": LEAD["lead_id"], "status": "approved", "reason": "合成测试审核"}]},
                       "report": {"summary": "没有确认需求"}, "stageCoverage": {"report": {"status": "completed"}}}
        self.storage.update_task(self.task, status="completed", result=self.result)
        self.storage.save_lead(self.task, copy.deepcopy(LEAD))

    def feedback(self, agent="lead_signal_agent"):
        fid = self.storage.create_lead_feedback(task_id=self.task, sales_user_id=7, lead_id=LEAD["lead_id"],
            comment_id=LEAD["comment_id"], feedback_type="missed_lead", expected_result="核对委托证据")
        runner = Runner({**COMMON, "diagnosis": "待核对本人委托", "target_agents": [agent],
                         "prompt_improvements": [improvement(agent)], "eval_case": {}, "recommended_action": "验收"})
        with patch("agent_harness.feedback.StructuredAgentRunner", return_value=runner):
            result = self.service.analyze(fid)
        return fid, result, runner

    def raw_run(self, classification):
        self.storage.save_agent_run(task_id=self.task, agent_key="lead_signal_agent", agent_name="fixture",
            prompt_version="fixture-original / policy:" + SERVICE_POLICY_VERSION, model="fixture",
            purpose="synthetic", input_summary="{}", output_summary=json.dumps({"leads": [{
                "candidate_id": "candidate-1", "classification": classification, "reason": "原始模型理由"}]}),
            confidence=.8, error="", duration_ms=1, run_type="main")

    def formal_review(self, *, task=None, status="approved", note="合成正式审核"):
        with self.storage.connect() as db:
            db.execute("INSERT INTO human_reviews(lead_id,basis,task_id,status,note,actor,created) VALUES(?,?,?,?,?,?,?)",
                       (LEAD["lead_id"], self.store.review_basis(LEAD, PLAN), task or self.task, status, note, 7, 1.0))

    def test_feedback_inherits_confirmed_scope_and_historical_versions(self):
        self.raw_run("uncertain")
        _, analyzed, runner = self.feedback()
        payload = runner.calls[0][1]
        self.assertEqual(payload["plan"], PLAN)
        self.assertEqual(payload["offeringId"], AGENT_CUSTOM_DEVELOPMENT)
        self.assertEqual(payload["searchGoal"], PLAN["searchGoal"])
        context = analyzed["analysis"]["learningContext"]
        self.assertEqual(context["servicePolicyVersion"], SERVICE_POLICY_VERSION)
        self.assertIn("fixture-original", context["promptVersions"][0]["promptVersion"])
        self.assertIn("critic_qa_agent", context["candidateBases"])

    def test_feedback_apply_is_idempotent_and_not_activation(self):
        fid, _, _ = self.feedback()
        before = self.registry.active_prompt("lead_signal_agent")["id"]
        first, second = self.service.apply(fid), self.service.apply(fid)
        self.assertEqual(first["candidatePromptId"], second["candidatePromptId"])
        self.assertFalse(first["promoted"])
        self.assertEqual(self.registry.active_prompt("lead_signal_agent")["id"], before)
        candidate = next(p for p in self.registry.prompt_versions() if p["id"] == first["candidatePromptId"])
        self.assertEqual(candidate["metrics"]["learningContext"]["confirmedPlan"], PLAN)
        self.assertEqual(candidate["metrics"]["evaluationStatus"], "awaiting_frozen_dataset")

    def test_report_feedback_has_a_supported_candidate_path(self):
        fid, analyzed, _ = self.feedback("report_agent")
        self.assertEqual(analyzed["analysis"]["target_agents"], ["report_agent"])
        cid = self.service.apply(fid)["candidatePromptId"]
        self.assertEqual(next(p for p in self.registry.prompt_versions() if p["id"] == cid)["agentKey"], "report_agent")

    def test_feedback_rejects_stale_analysis(self):
        fid, _, _ = self.feedback()
        active = self.registry.active_prompt("lead_signal_agent")
        other = self.storage.create_prompt_candidate(agent_key="lead_signal_agent", agent_name="fixture",
            base_version=active["version"], prompt="changed base", source_task_id=self.task, notes="synthetic")
        self.storage.promote_prompt_version(other)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.service.apply(fid)

    def test_rule_changed_output_cannot_be_patched_as_prompt_feedback(self):
        self.raw_run("irrelevant")
        fid, analyzed, _ = self.feedback()
        self.assertEqual(analyzed["analysis"]["causalFindings"][0]["layer"], "rule")
        with self.assertRaisesRegex(ValueError, "根因"):
            self.service.apply(fid)

    def test_full_original_decision_takes_precedence_over_run_summary(self):
        self.raw_run("uncertain")
        self.result["leads"]["leads"][0]["original_model_decision"] = {"classification": "irrelevant", "reason": "完整原始判定"}
        self.storage.update_task(self.task, result=self.result)
        runner = Runner(reflection())
        self.service.reflect_task(self.task, runner=runner)
        finding = runner.calls[0][1]["causalFindings"][0]
        self.assertEqual(finding["rawClassification"], "irrelevant")

    def test_business_review_request_is_not_a_quality_conflict(self):
        self.result["leads"]["leads"][0]["business_review_required"] = True
        self.result["qa"]["review_requests"] = [LEAD["lead_id"] + " 确认真实委托意向"]
        runner = Runner(reflection())
        self.service.reflect_task(self.task, result=self.result, runner=runner)
        self.assertFalse(runner.calls[0][1]["causalFindings"])

    def test_single_feedback_sends_only_target_and_parent_evidence(self):
        unrelated = {**LEAD, "lead_id": "note-2:comment-2", "note_id": "note-2", "comment_id": "comment-2",
                     "content": "unrelated-sensitive-fixture"}
        self.result["leads"]["leads"].append(unrelated)
        self.result["notes"] = [{"id": "note-1", "desc": "父笔记语境"}, {"id": "note-2", "desc": "unrelated-note-fixture"}]
        self.result["qa"]["issues"] = [{"target_id": unrelated["lead_id"], "message": "unrelated-issue-fixture"}]
        self.storage.update_task(self.task, result=self.result)
        _, _, runner = self.feedback()
        context = runner.calls[0][1]
        self.assertEqual(len(context["leadEvidence"]), 1)
        self.assertEqual(context["evidenceCoverage"]["total"], 2)
        self.assertEqual(context["noteEvidence"], [{"id": "note-1", "desc": "父笔记语境"}])
        serialized = json.dumps(context)
        for forbidden in ("unrelated-sensitive-fixture", "unrelated-note-fixture", "unrelated-issue-fixture"):
            self.assertNotIn(forbidden, serialized)

    def test_feedback_run_is_not_recorded_as_main(self):
        fid = self.storage.create_lead_feedback(task_id=self.task, sales_user_id=7, lead_id=LEAD["lead_id"],
                                               feedback_type="missed_lead")
        with patch("agent_harness.feedback.StructuredAgentRunner", return_value=Runner(COMMON)) as constructor:
            self.service.analyze(fid)
        self.assertEqual(constructor.call_args.kwargs["run_type"], "feedback")
        self.assertTrue(constructor.call_args.kwargs["run_id"].startswith(f"feedback-{fid}-"))

    def test_legacy_feedback_main_run_does_not_break_reflection_idempotency(self):
        runner = Runner(reflection())
        first = self.service.reflect_task(self.task, runner=runner)
        self.storage.save_agent_run(task_id=self.task, agent_key="feedback_learning_agent", agent_name="fixture",
            prompt_version="legacy-feedback", model="fixture", purpose="synthetic", input_summary="{}",
            output_summary="{}", confidence=.8, error="", duration_ms=1, run_type="main")
        self.assertEqual(self.service.reflect_task(self.task, runner=runner), first)
        self.assertEqual(len(runner.calls), 1)

    def test_formal_review_changes_reflection_hash_but_other_task_review_does_not(self):
        runner = Runner(reflection())
        initial = self.service.reflect_task(self.task, runner=runner)
        self.formal_review()
        changed = self.service.reflect_task(self.task, runner=runner)
        self.assertNotEqual(initial["evidenceHash"], changed["evidenceHash"])
        self.assertEqual(len(runner.calls), 2)
        self.formal_review(task=self.task + 100, note="其他任务审核不可混入")
        unchanged = self.service.reflect_task(self.task, runner=runner)
        self.assertEqual(unchanged, changed)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(len(runner.calls[-1][1]["humanReviews"]), 1)
        self.assertNotIn("actor", runner.calls[-1][1]["humanReviews"][0])

    def test_formal_review_conflicts_block_prompt_learning(self):
        self.formal_review(status="rejected")
        fid = self.storage.create_lead_feedback(task_id=self.task, sales_user_id=7, lead_id=LEAD["lead_id"],
                                               feedback_type="valid_lead")
        runner = Runner({**COMMON, "prompt_improvements": [improvement()]})
        with patch("agent_harness.feedback.StructuredAgentRunner", return_value=runner):
            analyzed = self.service.analyze(fid)
        self.assertTrue(any("正式人工审核" in f["message"] for f in analyzed["analysis"]["causalFindings"]))
        with self.assertRaisesRegex(ValueError, "根因"):
            self.service.apply(fid)

    def test_reflection_preserves_scope_and_deduplicates_success(self):
        runner = Runner(reflection(improvement()))
        first = self.service.reflect_task(self.task, runner=runner)
        second = self.service.reflect_task(self.task, runner=runner)
        self.assertEqual(first, second)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(first["status"], "awaiting_evaluation")
        self.assertEqual(len(first["candidatePromptIds"]), 1)
        self.assertEqual(runner.calls[0][1]["confirmedPlan"], PLAN)
        self.assertEqual(self.storage.get_task_summary(self.task)["evolution"], first)
        self.assertTrue(first["requiresEvaluation"])
        self.assertTrue(first["evidenceCoverage"]["complete"])

    def test_failure_requires_explicit_retry_and_never_enables(self):
        runner = Runner(ValueError("synthetic failure"))
        first = self.service.reflect_task(self.task, runner=runner)
        self.assertEqual(first["status"], "failed")
        self.service.reflect_task(self.task, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        runner.output = reflection()
        retried = self.service.reflect_task(self.task, runner=runner, retry=True)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(retried["candidatePromptIds"], [])

    def test_reflection_rule_conflict_blocks_alias_evidence(self):
        self.raw_run("irrelevant")
        runner = Runner(reflection(improvement(evidence_ids=["comment-1"])))
        result = self.service.reflect_task(self.task, runner=runner)
        self.assertEqual(result["candidatePromptIds"], [])
        self.assertTrue(result["blockedSuggestions"])
        self.assertEqual(self.storage.get_task_summary(self.task)["causalFindings"][0]["rawClassification"], "irrelevant")

    def test_generated_eval_case_is_only_unverified_development_advice(self):
        output = reflection()
        output["eval_cases"] = [{"case_type": "lead_signal", "input_excerpt": SOURCE,
                                 "expected_behavior": "需人工标注", "source_id": "comment-1"}]
        self.service.reflect_task(self.task, runner=Runner(output))
        case = self.storage.list_eval_cases()[0]["payload"]
        self.assertFalse(case["humanVerified"])
        self.assertFalse(case["eligibleForAcceptance"])
        self.assertEqual(self.store.datasets(), [])

    def test_unknown_agent_improvement_cannot_create_candidate(self):
        result = self.service.reflect_task(self.task, runner=Runner(reflection(improvement("planning_agent"))))
        self.assertFalse(result["candidatePromptIds"])

    def test_incomplete_reflection_is_failed_not_silent_success(self):
        result = self.service.reflect_task(self.task, runner=Runner(COMMON))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["candidatePromptIds"])

    def test_conflicting_human_feedback_requires_resolution(self):
        for kind in ("valid_lead", "invalid_lead"):
            self.storage.create_lead_feedback(task_id=self.task, sales_user_id=7, lead_id=LEAD["lead_id"],
                                              feedback_type=kind)
        runner = Runner(reflection(improvement()))
        result = self.service.reflect_task(self.task, runner=runner)
        self.assertFalse(result["candidatePromptIds"])
        context = runner.calls[0][1]
        self.assertEqual(len(context["humanFeedback"]), 2)
        self.assertTrue(any(f["layer"] == "feedback" for f in context["causalFindings"]))

    def test_relevance_advice_can_reference_note_without_collected_comment(self):
        self.result["notes"] = [{"id": "note-only", "platform": "xhs", "title": "寻找开发团队", "desc": SOURCE}]
        self.result["leads"] = {"leads": []}
        runner = Runner(reflection(improvement("relevance_agent", evidence_ids=["note-only"])))
        result = self.service.reflect_task(self.task, result=self.result, runner=runner)
        self.assertEqual(len(result["candidatePromptIds"]), 1)
        self.assertEqual(runner.calls[0][1]["noteEvidence"][0]["id"], "note-only")

    def test_rollback_rejects_unactivated_candidate(self):
        current = self.registry.active_prompt("lead_signal_agent")
        cid = self.registry.create_learning_candidate("lead_signal_agent", "synthetic patch", context=PLAN,
            source_key="fixture", source_task_id=self.task)
        with self.assertRaisesRegex(ValueError, "曾实际启用"):
            self.registry.rollback("lead_signal_agent", cid)
        with self.assertRaises(ValueError):
            self.registry.rollback("report_agent", current["id"])
        self.storage.promote_prompt_version(cid)  # Fixture models an already-authorized historical activation.
        self.assertEqual(self.registry.rollback("lead_signal_agent", current["id"])["id"], current["id"])

    def test_active_focused_prompt_falls_back_for_general_requests(self):
        base = self.registry.active_prompt("lead_signal_agent")
        cid = self.registry.create_learning_candidate("lead_signal_agent", "FOCUSED_ONLY_PATCH", context=PLAN,
            source_key="focused-scope-fixture", source_task_id=self.task)
        self.storage.promote_prompt_version(cid)
        self.assertEqual(self.registry.effective_prompt("lead_signal_agent", PLAN)["id"], cid)
        self.assertEqual(self.registry.effective_prompt("lead_signal_agent", {"offeringId": "general"})["id"], base["id"])

    def test_feedback_sees_effective_scope_prompt_but_tracks_global_base(self):
        base = self.registry.active_prompt("lead_signal_agent")
        general_id = self.registry.create_learning_candidate("lead_signal_agent", "GENERAL_ONLY_PATCH",
            context={"offeringId": "general"}, source_key="feedback-general-fixture", source_task_id=self.task)
        global_active = self.storage.promote_prompt_version(general_id)
        _, analyzed, runner = self.feedback()
        shown = next(row for row in runner.calls[0][1]["activePrompts"] if row["agentKey"] == "lead_signal_agent")
        self.assertEqual(shown["version"], base["version"])
        self.assertNotIn("GENERAL_ONLY_PATCH", shown["prompt"])
        tracked = analyzed["analysis"]["learningContext"]["candidateBases"]["lead_signal_agent"]
        self.assertEqual(tracked["version"], global_active["version"])

    def test_cross_scope_candidate_composition_never_embeds_other_scope_patch(self):
        focused_id = self.registry.create_learning_candidate("lead_signal_agent", "FOCUSED_ONLY_PATCH", context=PLAN,
            source_key="focused-fixture", source_task_id=self.task)
        self.storage.promote_prompt_version(focused_id)
        general_id = self.registry.create_learning_candidate("lead_signal_agent", "GENERAL_ONLY_PATCH",
            context={"offeringId": "general"}, source_key="general-fixture", source_task_id=self.task)
        general = next(p for p in self.registry.prompt_versions() if p["id"] == general_id)
        self.assertNotIn("FOCUSED_ONLY_PATCH", general["prompt"])
        self.storage.promote_prompt_version(general_id)
        self.assertEqual(self.registry.effective_prompt("lead_signal_agent", PLAN)["id"], focused_id)
        self.assertEqual(self.registry.effective_prompt("lead_signal_agent", {})["id"], general_id)

    def test_scope_fallback_skips_a_disabled_historical_prompt(self):
        base = self.registry.active_prompt("lead_signal_agent")
        focused_id = self.registry.create_learning_candidate("lead_signal_agent", "FOCUSED_DISABLED_PATCH", context=PLAN,
            source_key="focused-disabled-fixture", source_task_id=self.task)
        self.storage.promote_prompt_version(focused_id)
        general_id = self.registry.create_learning_candidate("lead_signal_agent", "GENERAL_ACTIVE_PATCH",
            context={"offeringId": "general"}, source_key="general-active-fixture", source_task_id=self.task)
        self.storage.promote_prompt_version(general_id)
        self.registry.disable(focused_id)
        effective = self.registry.effective_prompt("lead_signal_agent", PLAN)
        self.assertEqual(effective["id"], base["id"])
        self.assertNotIn("FOCUSED_DISABLED_PATCH", effective["prompt"])


class SpecialistEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = SalesStorage(Path(self.tmp.name) / "fixture.db")
        self.store = WorkbenchStore(self.storage)
        self.registry = PromptRegistry(self.storage)
        self.registry.ensure_defaults()

    def samples(self, agent="critic_qa_agent", count=100, offering=AGENT_CUSTOM_DEVELOPMENT):
        return [{"id": f"sample-{i}", "noteId": f"note-{i}", "topic": f"topic-{i % 3}",
                 "sourceUrl": f"https://fixture.invalid/{i}", "collectedAt": "synthetic", "text": SOURCE,
                 "split": "development" if i < 40 else "acceptance", "annotator": "synthetic-test",
                 "sourceVerified": True, "offeringId": offering, "agentKey": agent,
                 "input": {"plan": {**PLAN, "offeringId": offering}, "leads": [{"lead_id": "c1", "content": SOURCE}],
                           "collectionSummary": {"notesCollected": 1}},
                 "expected": ({"approved": False, "issueTargets": ["c1"]} if agent == "critic_qa_agent"
                              else {"requiredFacts": ["合格需求为0"], "forbiddenClaims": ["已经成交"]})}
                for i in range(count)]

    def candidate(self, agent):
        return self.registry.create_learning_candidate(agent, "synthetic scoped patch", context=PLAN,
                                                       source_key="fixture:" + agent, source_task_id=0)

    def evaluate(self, samples, agent, output):
        dataset = self.store.import_dataset({"name": "synthetic only", "samples": samples})
        cid = self.candidate(agent)
        runner = Runner(output)
        result = HarnessEvaluator(self.storage).run_dataset({"llm": {"model": "synthetic"}},
            {"datasetId": dataset["datasetId"], "promptId": cid}, runner_factory=lambda registry: runner)
        return result, cid, runner

    def test_qa_is_really_run_and_needs_human_quality_review(self):
        output = {**COMMON, "approved": False, "issues": [{"level": "error", "target_id": "c1", "message": "引用不足"}],
                  "review_requests": [{"target_id": "c1", "reason": "引用不足", "requested_action": "核对原文"}],
                  "reviews": [{"target_id": "c1", "status": "rejected", "reason": "引用不足"}]}
        result, cid, runner = self.evaluate(self.samples(), "critic_qa_agent", output)
        self.assertEqual(len(runner.calls), 60)
        self.assertEqual({key for key, _ in runner.calls}, {"critic_qa_agent"})
        self.assertTrue(result["autoPassed"])
        self.assertFalse(result["passed"])
        self.assertFalse(result["qualityReviewComplete"])
        self.assertIsNone(result["rows"][0]["citationPassed"])
        self.assertEqual(result["rows"][0]["inputContext"]["offeringId"], AGENT_CUSTOM_DEVELOPMENT)
        with self.assertRaises(ValueError):
            self.registry.promote_evaluated(cid, result["runId"])

    def test_report_constraints_fail_on_unsupported_claim(self):
        output = {**COMMON, "summary": "合格需求为0，但已经成交", "insights": [], "recommended_actions": []}
        result, _, runner = self.evaluate(self.samples("report_agent"), "report_agent", output)
        self.assertEqual(len(runner.calls), 60)
        self.assertFalse(result["autoPassed"])
        self.assertEqual(result["metrics"]["machineChecksPassed"], 0)

    def test_malformed_specialist_output_fails_samples(self):
        result, _, _ = self.evaluate(self.samples(), "critic_qa_agent", COMMON)
        self.assertFalse(result["autoPassed"])
        self.assertEqual(result["metrics"]["failedSamples"], 60)

    def test_classification_dataset_cannot_certify_qa(self):
        samples = self.samples()
        for sample in samples:
            sample.pop("agentKey")
            sample["labels"] = {"relevant": True, "classification": "demand"}
        dataset = self.store.import_dataset({"samples": samples})
        with self.assertRaisesRegex(ValueError, "待准备"):
            HarnessEvaluator(self.storage).run_dataset({}, {"datasetId": dataset["datasetId"],
                "promptId": self.candidate("critic_qa_agent")}, runner_factory=lambda registry: Runner(COMMON))

    def test_other_service_samples_cannot_pad_focused_acceptance(self):
        samples = self.samples(count=41) + [{**s, "id": "g-" + s["id"], "noteId": "g-" + s["noteId"]}
                                          for s in self.samples(count=100, offering="general")]
        output = {**COMMON, "approved": False, "issues": [{"level": "error", "target_id": "c1", "message": "错误"}],
                  "review_requests": [], "reviews": [{"target_id": "c1", "status": "rejected", "reason": "错误"}]}
        result, _, runner = self.evaluate(samples, "critic_qa_agent", output)
        self.assertEqual(len(runner.calls), 1)
        self.assertFalse(result["requirements"]["atLeast100Samples"])
        self.assertFalse(result["requirements"]["acceptance60"])
        self.assertFalse(result["autoPassed"])

    def test_reviewed_frozen_report_can_activate_then_rollback(self):
        output = {**COMMON, "summary": "合格需求为0", "insights": [], "recommended_actions": []}
        previous = self.registry.active_prompt("report_agent")["id"]
        result, cid, _ = self.evaluate(self.samples("report_agent"), "report_agent", output)
        self.assertTrue(result["autoPassed"])
        review = {"runId": result["runId"], "reviewedSampleIds": [r["id"] for r in result["rows"]],
                  "citationsVerified": True, "noFabrication": True, "note": "Synthetic review fixture, not real acceptance",
                  "qualityReviews": [{"sampleId": r["id"], "passed": True, "note": "Synthetic checked output"}
                                     for r in result["rows"]]}
        accepted = self.store.review_benchmark(review, actor=1)
        self.assertTrue(accepted["qualityReviewComplete"])
        self.assertEqual(self.registry.promote_evaluated(cid, result["runId"])["id"], cid)
        self.assertEqual(self.registry.rollback("report_agent", previous)["id"], previous)

    def test_checked_promotion_rejects_stale_base_after_successful_acceptance(self):
        output = {**COMMON, "summary": "合格需求为0", "insights": [], "recommended_actions": []}
        result, cid, _ = self.evaluate(self.samples("report_agent"), "report_agent", output)
        self.store.review_benchmark({"runId": result["runId"],
            "reviewedSampleIds": [r["id"] for r in result["rows"]], "citationsVerified": True,
            "noFabrication": True, "note": "Synthetic fixture",
            "qualityReviews": [{"sampleId": r["id"], "passed": True, "note": "Synthetic checked"}
                               for r in result["rows"]]}, actor=1)
        other = self.registry.create_learning_candidate("report_agent", "Different synthetic base", context=PLAN,
                                                         source_key="another-fixture", source_task_id=0)
        self.storage.promote_prompt_version(other)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.registry.promote_evaluated(cid, result["runId"])

    def test_failed_human_quality_review_cannot_activate_matching_report(self):
        output = {**COMMON, "summary": "合格需求为0", "insights": [], "recommended_actions": []}
        result, cid, _ = self.evaluate(self.samples("report_agent"), "report_agent", output)
        reviewed = self.store.review_benchmark({"runId": result["runId"],
            "reviewedSampleIds": [r["id"] for r in result["rows"]], "citationsVerified": True,
            "noFabrication": True, "note": "Synthetic fixture: wording is misleading",
            "qualityReviews": [{"sampleId": r["id"], "passed": index != 0, "note": "Synthetic verdict"}
                               for index, r in enumerate(result["rows"])]}, actor=1)
        self.assertFalse(reviewed["passed"])
        with self.assertRaises(ValueError):
            self.registry.promote_evaluated(cid, result["runId"])

    def test_qa_requires_complete_unique_per_lead_reviews(self):
        expected = {"approved": True, "issueTargets": []}
        request = {"leads": [{"lead_id": "c1"}, {"lead_id": "c2"}]}
        good = {**COMMON, "approved": True, "issues": [], "review_requests": [], "reviews": [
            {"target_id": target, "status": "approved", "reason": "合成核验"} for target in ("c1", "c2")]}
        self.assertTrue(HarnessEvaluator._specialised_check("critic_qa_agent", good, expected, request))
        for rows in (None, [], good["reviews"][:1], [good["reviews"][0]] * 2,
                     [good["reviews"][0], {"target_id": "unknown", "status": "approved", "reason": "未知"}]):
            with self.subTest(rows=rows):
                self.assertFalse(HarnessEvaluator._specialised_check("critic_qa_agent", {**good, "reviews": rows}, expected, request))

    def test_frozen_run_preserves_scope_fallback_chain_after_concurrent_disable(self):
        base = self.registry.active_prompt("lead_signal_agent")
        focused_id = self.candidate("lead_signal_agent")
        self.storage.promote_prompt_version(focused_id)
        samples = self.samples(agent="lead_signal_agent", offering="general")
        for sample in samples:
            sample["labels"] = {"relevant": True, "classification": "demand"}
        dataset = self.store.import_dataset({"name": "synthetic scope freeze", "samples": samples})
        observed = []
        storage = self.storage

        class FrozenRunner:
            def __init__(self, registry):
                self.registry = registry

            def run(self, key, data, purpose=""):
                if key == "relevance_agent":
                    return {**COMMON, "notes": [{"note_id": data["notes"][0]["note_id"], "decision": "keep"}]}
                observed.append(self.registry.effective_prompt(key, data)["id"])
                if len(observed) == 1:
                    storage.disable_prompt_version(base["id"])
                return {**COMMON, "leads": [{"candidate_id": data["leadCandidates"][0]["candidate_id"],
                                            "classification": "demand", "evidence": [SOURCE]}]}

        result = HarnessEvaluator(self.storage).run_dataset({}, {"datasetId": dataset["datasetId"]},
                                                           runner_factory=FrozenRunner)
        self.assertTrue(result["autoPassed"])
        self.assertEqual(observed, [base["id"]] * 60)
        actual = result["effectivePromptVersions"]["general"]["lead_signal_agent"]
        self.assertEqual(actual["id"], base["id"])
        self.assertNotEqual(actual["version"], result["promptVersions"]["lead_signal_agent"])


if __name__ == "__main__":
    unittest.main()
