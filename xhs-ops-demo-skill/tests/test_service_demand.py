"""Synthetic tests for a single-service buyer-intent gate; no platform or model calls."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_harness.agents import DEFAULT_AGENT_SPECS
from agent_harness.evaluator import HarnessEvaluator
from agent_harness.offering import AGENT_CUSTOM_DEVELOPMENT, SERVICE_POLICY_VERSION, apply_offering_decision
from agent_harness.planning import normalize_plan, plan_search
from agent_harness.registry import PromptRegistry
from storage import SalesStorage
from workbench_store import WorkbenchStore


SCENARIO = "我们门店需要一个自动汇总预约并提醒店员的智能体"
COMMISSION = "想请开发者帮忙定制，能报个价吗"
FOCUSED = {"offeringId": AGENT_CUSTOM_DEVELOPMENT}


def candidate(content=None, **overrides):
    return {
        "candidate_id": "xhs:fixture-note:fixture-comment",
        "lead_type": "comment_lead",
        "content": content if content is not None else f"{SCENARIO}。{COMMISSION}？",
        "note": {
            "title": "智能体开发经验",
            "desc": "这是另一位作者的笔记内容，不能当作评论作者的委托证据。",
        },
        **overrides,
    }


def decision(**overrides):
    return {
        "decision": "lead",
        "classification": "demand",
        "score": 95,
        "reason": "模型原始判定依据",
        "intent_role": "buyer",
        "service_fit": "custom_development",
        "demand_origin": "self",
        "customization_evidence": [SCENARIO],
        "commissioning_evidence": [COMMISSION],
        "evidence": [SCENARIO, COMMISSION],
        **overrides,
    }


class ServiceDemandTests(unittest.TestCase):
    def assert_not_qualified(self, output, classification="uncertain"):
        self.assertEqual(output["classification"], classification)
        self.assertNotEqual(output["decision"], "lead")

    def test_valid_buyer_commission_does_not_require_a_stated_budget(self):
        source, original = candidate(), decision()
        self.assertNotIn("预算", source["content"])
        self.assertNotIn("budget", original)
        output = apply_offering_decision(source, original, FOCUSED)
        self.assertEqual(output["classification"], "demand")
        self.assertEqual(output["decision"], "lead")

    def test_note_author_can_qualify_with_their_own_text(self):
        output = apply_offering_decision(candidate(lead_type="note_author"), decision(), FOCUSED)
        self.assertEqual(output["classification"], "demand")
        self.assertEqual(output["decision"], "lead")

    def test_high_score_supplier_is_promotion(self):
        output = apply_offering_decision(candidate(), decision(intent_role="supplier", score=100), FOCUSED)
        self.assert_not_qualified(output, "promotion")

    def test_high_score_discussion_is_information(self):
        output = apply_offering_decision(candidate(), decision(intent_role="discussion", score=100), FOCUSED)
        self.assert_not_qualified(output, "information")

    def test_discussion_stays_information_when_not_buying_custom_development(self):
        output = apply_offering_decision(
            candidate(), decision(intent_role="discussion", service_fit="not_fit", score=100), FOCUSED
        )
        self.assert_not_qualified(output, "information")

    def test_nonmatching_service_is_irrelevant(self):
        output = apply_offering_decision(candidate(), decision(service_fit="not_fit"), FOCUSED)
        self.assert_not_qualified(output, "irrelevant")

    def test_unknown_buyer_role_cannot_pass_on_score_alone(self):
        output = apply_offering_decision(candidate(), decision(intent_role="unclear", score=100), FOCUSED)
        self.assert_not_qualified(output)

    def test_missing_semantic_fields_are_not_assumed(self):
        for field in ("intent_role", "service_fit", "demand_origin"):
            with self.subTest(field=field):
                value = decision()
                value.pop(field)
                self.assert_not_qualified(apply_offering_decision(candidate(), value, FOCUSED))

    def test_missing_evidence_arrays_are_not_assumed(self):
        for field in ("customization_evidence", "commissioning_evidence"):
            with self.subTest(field=field):
                value = decision()
                value.pop(field)
                self.assert_not_qualified(apply_offering_decision(candidate(), value, FOCUSED))

    def test_evidence_arrays_must_be_nonempty_strings(self):
        for field in ("customization_evidence", "commissioning_evidence"):
            for invalid in (None, [], "", SCENARIO, [None], [1], [""], ["   "], {"quote": SCENARIO}):
                with self.subTest(field=field, invalid_type=type(invalid).__name__):
                    self.assert_not_qualified(
                        apply_offering_decision(candidate(), decision(**{field: invalid}), FOCUSED)
                    )

    def test_fabricated_or_paraphrased_quote_cannot_qualify(self):
        output = apply_offering_decision(
            candidate(), decision(commissioning_evidence=["我们已经确定外包开发，请立即签合同"]), FOCUSED
        )
        self.assert_not_qualified(output)

    def test_every_quote_must_match_not_just_one_valid_quote(self):
        for field, valid in (("customization_evidence", SCENARIO), ("commissioning_evidence", COMMISSION)):
            with self.subTest(field=field):
                output = apply_offering_decision(
                    candidate(), decision(**{field: [valid, "原文并不存在的证据"]}), FOCUSED
                )
                self.assert_not_qualified(output)

    def test_note_context_cannot_supply_comment_author_quotes(self):
        source = candidate(
            "我想了解一下这个。",
            note={"title": SCENARIO, "desc": COMMISSION},
        )
        self.assert_not_qualified(apply_offering_decision(source, decision(), FOCUSED))

    def test_short_commission_without_own_custom_scenario_stays_uncertain(self):
        source = candidate(COMMISSION, note={"title": SCENARIO, "desc": "相关场景演示"})
        self.assert_not_qualified(apply_offering_decision(source, decision(), FOCUSED))

    def test_own_custom_scenario_without_commission_stays_uncertain(self):
        source = candidate(f"{SCENARIO}，准备自己学着搭建。")
        self.assert_not_qualified(apply_offering_decision(source, decision(), FOCUSED))

    def test_valid_third_party_quotes_do_not_prove_the_authors_demand(self):
        source = candidate(f"转述朋友的原话：{SCENARIO}，{COMMISSION}。我自己没有这个需求。")
        output = apply_offering_decision(source, decision(demand_origin="third_party"), FOCUSED)
        self.assert_not_qualified(output)

    def test_unclear_quote_attribution_stays_uncertain(self):
        output = apply_offering_decision(candidate(), decision(demand_origin="unclear"), FOCUSED)
        self.assert_not_qualified(output)

    def test_gate_does_not_upgrade_existing_nonlead_classification(self):
        for overrides in (
            {"classification": "uncertain", "decision": "ignore"},
            {"classification": "information", "decision": "ignore"},
            {"classification": "demand", "decision": "below_threshold"},
        ):
            with self.subTest(overrides=overrides):
                output = apply_offering_decision(candidate(), decision(**overrides), FOCUSED)
                self.assertNotEqual(output["decision"], "lead")

    def test_downgrade_keeps_original_reason_and_adds_intent_reason(self):
        original = decision(commissioning_evidence=[])
        output = apply_offering_decision(candidate(), original, FOCUSED)
        self.assertEqual(output["reason"], "模型原始判定依据")
        self.assertTrue(output.get("intent_reason"))

    def test_general_and_legacy_plans_do_not_apply_the_focused_gate(self):
        original = {"decision": "lead", "classification": "demand", "score": 90, "reason": "一般研究需求"}
        for plan in ({}, {"offeringId": "general"}):
            with self.subTest(plan=plan):
                self.assertEqual(apply_offering_decision(candidate("一般讨论内容"), deepcopy(original), plan), original)

    def test_normalize_plan_defaults_legacy_plan_to_general(self):
        self.assertEqual(normalize_plan({"keywords": ["智能体"]})["offeringId"], "general")

    def test_normalize_plan_preserves_explicit_focused_offering(self):
        output = normalize_plan({"keywords": ["智能体定制"], "offeringId": AGENT_CUSTOM_DEVELOPMENT})
        self.assertEqual(output["offeringId"], AGENT_CUSTOM_DEVELOPMENT)

    def test_failed_model_planning_is_a_manual_draft_requiring_confirmation(self):
        with patch("agent_harness.planning.StructuredAgentRunner") as runner:
            runner.return_value.run.side_effect = RuntimeError("fixture model unavailable")
            output = plan_search({}, {"seedKeywords": ["智能体"], "offeringId": AGENT_CUSTOM_DEVELOPMENT,
                                      "maxKeywords": 3})
        self.assertEqual(output["status"], "draft")
        self.assertEqual(output["planningMode"], "manual")
        self.assertIs(output["requiresConfirmation"], True)
        self.assertEqual(output["offeringId"], AGENT_CUSTOM_DEVELOPMENT)
        self.assertLessEqual(len(output["keywords"]), 3)
        self.assertTrue(output["error"])
        self.assertTrue(all("智能体" in word for word in output["keywords"]))


class StoredPrompt:
    def __init__(self, row):
        self.row = row

    def get_active_prompt(self, agent_key):
        return self.row


class ServiceDemandPromptRegistryTests(unittest.TestCase):
    def test_compatible_old_builtin_keyword_prompt_stays_active(self):
        key = "lead_signal_agent"
        row = {
            "id": 4, "agentKey": key, "version": "lead-signal-agent-keyword-v3",
            "prompt": "old built-in prompt", "notes": "Built-in Harness prompt.", "status": "active",
        }
        active = PromptRegistry(StoredPrompt(row)).active_prompt(key)
        self.assertEqual(active, row)

    def test_current_builtin_record_remains_active(self):
        key = "lead_signal_agent"
        spec = DEFAULT_AGENT_SPECS[key]
        row = {
            "id": 5, "agentKey": key, "version": spec.version, "prompt": spec.system_prompt,
            "notes": "Built-in Harness prompt.", "status": "active",
        }
        self.assertEqual(PromptRegistry(StoredPrompt(row)).active_prompt(key), row)

    def test_human_custom_active_prompt_is_not_silently_replaced(self):
        key = "lead_signal_agent"
        row = {
            "id": 6, "agentKey": key, "version": "human-custom-v1",
            "prompt": "A human-approved custom prompt", "notes": "管理员逐条核验后启用", "status": "active",
        }
        self.assertEqual(PromptRegistry(StoredPrompt(row)).active_prompt(key), row)

    def test_disabling_new_builtin_candidate_does_not_silently_use_it(self):
        key = "lead_signal_agent"
        with tempfile.TemporaryDirectory() as temporary:
            storage = SalesStorage(Path(temporary) / "fixture-prompts.db")
            old_id = storage.ensure_prompt_version(
                agent_key=key, agent_name="fixture", version="lead-signal-agent-keyword-v3",
                prompt="compatible old fixture", status="active", score=0,
                notes="Built-in Harness prompt.",
            )
            registry = PromptRegistry(storage)
            registry.ensure_defaults()
            new_row = next(row for row in registry.prompt_versions(key)
                           if row["version"] == DEFAULT_AGENT_SPECS[key].version)
            self.assertEqual(new_row["status"], "candidate")
            self.assertEqual(registry.active_prompt(key)["id"], old_id)
            registry.disable(new_row["id"])
            self.assertEqual(registry.active_prompt(key)["id"], old_id)
            self.assertEqual(next(row for row in registry.prompt_versions(key)
                                  if row["id"] == new_row["id"])["status"], "disabled")


class ServiceDemandReviewCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = SalesStorage(Path(self.tmp.name) / "fixture.db")
        self.store = WorkbenchStore(self.storage)
        self.plan = {
            "seedKeywords": ["智能体"], "keywords": ["智能体定制"],
            "searchGoal": "查找开发相关需求", "excludeKeywords": [],
        }
        self.lead = {
            "lead_id": "fixture-note:fixture-comment", "platform": "xhs",
            "note_id": "fixture-note", "comment_id": "fixture-comment",
            "content": f"{SCENARIO}。{COMMISSION}", "evidence": [SCENARIO, COMMISSION],
            "note_url": "https://www.xiaohongshu.com/explore/fixture-note",
        }
        self.result = {"leads": {"leads": [self.lead]}}
        task_id = self.storage.create_task({"confirmedPlan": self.plan}, sales_user_id=0)
        self.storage.update_task(task_id, result=self.result)
        self.store.review(
            {"taskId": task_id, "leadId": self.lead["lead_id"], "status": "approved",
             "note": "合成审核记录，仅用于兼容性测试"}, 1
        )

    def test_existing_general_approval_survives_explicit_general_default(self):
        for plan in (self.plan, {**self.plan, "offeringId": "general"}):
            with self.subTest(explicit_offering="offeringId" in plan):
                result = self.store.annotate(deepcopy(self.result), plan)
                self.assertEqual(result["leads"]["leads"][0]["review"]["status"], "approved")
        self.assertEqual(WorkbenchStore.review_basis(self.lead, self.plan),
                         WorkbenchStore.review_basis(self.lead, {**self.plan, "offeringId": "general"}))

    def test_general_approval_does_not_transfer_to_focused_service(self):
        focused_plan = {**self.plan, "offeringId": AGENT_CUSTOM_DEVELOPMENT}
        result = self.store.annotate(deepcopy(self.result), focused_plan)
        self.assertEqual(result["leads"]["leads"][0]["review"]["status"], "pending")
        self.assertNotEqual(WorkbenchStore.review_basis(self.lead, self.plan),
                            WorkbenchStore.review_basis(self.lead, focused_plan))
        self.assertEqual(len(self.store.review_history(self.lead["lead_id"])), 1)


class ServiceDemandEvaluationTests(unittest.TestCase):
    def test_focused_evaluation_applies_same_evidence_gate_to_high_score_prediction(self):
        calls = []

        class Runner:
            def run(self, agent_key, payload, **kwargs):
                calls.append((agent_key, payload))
                if agent_key == "relevance_agent":
                    return {"notes": [{"note_id": "fixture-sample", "decision": "keep"}]}
                return {"leads": [decision(
                    candidate_id="fixture-sample", score=100,
                    commissioning_evidence=[], evidence=[SCENARIO],
                )]}

        with tempfile.TemporaryDirectory() as tmp:
            storage = SalesStorage(Path(tmp) / "fixture-eval.db")
            store = WorkbenchStore(storage)
            saved = store.import_dataset({"name": "合成规则测试，不是真实验收集", "samples": [{
                "id": "fixture-sample", "noteId": "fixture-note", "topic": "门店预约",
                "sourceUrl": "https://www.xiaohongshu.com/explore/fixture-note",
                "collectedAt": "2026-10-20", "text": f"{SCENARIO}，准备自己学习搭建。",
                "split": "acceptance", "labels": {"relevant": True, "classification": "uncertain"},
                "annotator": "unit-test", "sourceVerified": False,
                "offeringId": AGENT_CUSTOM_DEVELOPMENT,
            }]})
            result = HarnessEvaluator(storage).run_dataset(
                {}, {"datasetId": saved["datasetId"]}, lambda registry: Runner()
            )
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(payload["offeringId"] == AGENT_CUSTOM_DEVELOPMENT for _, payload in calls))
        self.assertEqual(result["servicePolicyVersion"], SERVICE_POLICY_VERSION)
        self.assertEqual(result["rows"][0]["classification"], "uncertain")
        self.assertNotEqual(result["rows"][0]["prediction"]["decision"], "lead")
        self.assertFalse(result["passed"])
        self.assertFalse(result["autoPassed"])

    def test_evaluation_policy_metadata_only_describes_executed_split(self):
        class Runner:
            def run(self, agent_key, payload, **kwargs):
                if agent_key == "relevance_agent":
                    return {"notes": [{"note_id": payload["notes"][0]["note_id"], "decision": "keep"}]}
                return {"leads": [decision(candidate_id=payload["leadCandidates"][0]["candidate_id"])]}

        samples = []
        for split, offering in (("development", AGENT_CUSTOM_DEVELOPMENT), ("acceptance", "general")):
            samples.append({
                "id": f"fixture-{split}", "noteId": f"fixture-note-{split}", "topic": "门店预约",
                "sourceUrl": f"https://www.xiaohongshu.com/explore/fixture-note-{split}",
                "collectedAt": "2026-10-20", "text": f"{SCENARIO}。{COMMISSION}",
                "split": split, "labels": {"relevant": True, "classification": "demand"},
                "annotator": "unit-test", "sourceVerified": False, "offeringId": offering,
            })
        with tempfile.TemporaryDirectory() as tmp:
            storage = SalesStorage(Path(tmp) / "fixture-eval-splits.db")
            saved = WorkbenchStore(storage).import_dataset({"name": "合成分组规则测试", "samples": samples})
            evaluator = HarnessEvaluator(storage)
            acceptance = evaluator.run_dataset(
                {}, {"datasetId": saved["datasetId"], "split": "acceptance"}, lambda registry: Runner()
            )
            development = evaluator.run_dataset(
                {}, {"datasetId": saved["datasetId"], "split": "development"}, lambda registry: Runner()
            )
        self.assertIsNone(acceptance["servicePolicyVersion"])
        self.assertEqual(acceptance["offeringCounts"], {"general": 1})
        self.assertEqual([row["offeringId"] for row in acceptance["rows"]], ["general"])
        self.assertEqual(development["servicePolicyVersion"], SERVICE_POLICY_VERSION)
        self.assertEqual(development["offeringCounts"], {AGENT_CUSTOM_DEVELOPMENT: 1})
        self.assertEqual([row["offeringId"] for row in development["rows"]], [AGENT_CUSTOM_DEVELOPMENT])


if __name__ == "__main__":
    unittest.main()
