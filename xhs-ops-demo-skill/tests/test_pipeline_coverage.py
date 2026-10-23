from __future__ import annotations

import copy
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agent_harness.pipeline import HarnessPipeline
from agent_harness.planning import normalize_plan


def note(identity, desc="完整原文及需求描述", title="智能体讨论"):
    return {"id": identity, "model_type": "note", "note_card": {
        "title": title, "desc": desc, "user": {"user_id": "fixture-user", "nickname": "fixture"},
        "interact_info": {},
    }}


class Adapter:
    def __init__(self, pages=None, comments=0, body="完整原文及需求描述"):
        self.pages = pages or (lambda keyword, page: [note(f"{keyword}-{page}-{i}") for i in range(30)])
        self.comment_count = comments
        self.body = body
        self.calls = []

    def ensure_authenticated(self):
        return None

    def search(self, keyword, page=1, sort="popular"):
        self.calls.append((keyword, page))
        return {"data": {"items": self.pages(keyword, page), "has_more": page < 2}}

    def read_note(self, identity):
        return {"data": {"items": [note(identity, self.body)]}}

    def comments(self, identity, cursor=""):
        return {"data": {"comments": [{"id": f"c-{i}", "content": f"评论 {i} " + "完整证据" * 400,
                                      "user_info": {"user_id": f"u-{i}"}} for i in range(self.comment_count)],
                         "has_more": False}}


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, key, payload, purpose=""):
        self.calls.append((key, copy.deepcopy(payload)))
        common = {"decision": "complete", "confidence": .9, "evidence": [], "risks": [], "next_actions": []}
        if key == "relevance_agent":
            return {**common, "notes": [{"note_id": n["note_id"], "decision": "keep", "reason": "相关"}
                                       for n in payload["notes"]]}
        if key == "lead_signal_agent":
            return {**common, "leads": [
                {"candidate_id": candidate["candidate_id"], "decision": "lead", "classification": "demand",
                 "score": 90, "evidence": [candidate["content"]], "reason": "原文支持"}
                for candidate in payload["leadCandidates"]]}
        if key == "critic_qa_agent":
            return {**common, "approved": True, "issues": [], "review_requests": [],
                "reviews": [{"target_id": lead["lead_id"], "status": "approved", "reason": "原文支持"}
                            for lead in payload["leads"]]}
        return {**common, "summary": "fixture report", "insights": [], "recommended_actions": []}


class PipelineCoverageTests(unittest.TestCase):
    def run_pipeline(self, *, adapter=None, runner=None, **options):
        plan = normalize_plan({"seedKeywords": ["词一"], "keywords": ["词一"], "maxNotes": 20,
                               "maxPagesPerKeyword": 1, "maxCommentsPerNote": 50, "maxCommentsTotal": 100,
                               "delayMin": 0, "delayMax": 0, "scanLeads": False, **options})
        runner = runner or Runner()
        pipeline = HarnessPipeline(adapter or Adapter(), {}, runner=runner)
        snapshots = []
        result = pipeline.run({"confirmedPlan": plan}, snapshot_callback=snapshots.append)
        return result, runner, snapshots

    def test_all_six_keywords_get_a_fair_share_of_one_global_budget(self):
        keywords = [f"词{i}" for i in range(6)]
        adapter = Adapter()
        result, _, _ = self.run_pipeline(adapter=adapter, keywords=keywords)
        self.assertEqual(adapter.calls, [(word, 1) for word in keywords])
        counts = Counter(n["source_keyword"] for n in result["notes"])
        self.assertEqual(sum(counts.values()), 20)
        self.assertEqual(set(counts), set(keywords))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        self.assertEqual(result["stageCoverage"]["search"], {
            "status": "completed", "completed": 6, "total": 6, "unit": "query",
        })

    def test_empty_keyword_share_is_filled_from_already_received_overflow(self):
        adapter = Adapter(pages=lambda word, page: [note(f"n-{i}") for i in range(30)] if word == "broad" else [])
        result, _, _ = self.run_pipeline(adapter=adapter, keywords=["broad", "empty"], maxNotes=7)
        self.assertEqual(adapter.calls, [("broad", 1), ("empty", 1)])
        self.assertEqual(len(result["notes"]), 7)
        self.assertEqual({n["source_keyword"] for n in result["notes"]}, {"broad"})
        self.assertEqual(result["stageCoverage"]["search"]["completed"], 2)

    def test_duplicate_first_pages_do_not_hide_unique_second_pages(self):
        adapter = Adapter(pages=lambda word, page: [note("shared")] if page == 1 else
                          [note(f"{word}-unique-{i}") for i in range(4)])
        result, _, _ = self.run_pipeline(adapter=adapter, keywords=["a", "b"], maxNotes=6, maxPagesPerKeyword=2)
        self.assertEqual(adapter.calls, [("a", 1), ("b", 1), ("a", 2), ("b", 2)])
        self.assertEqual(len({n["id"] for n in result["notes"]}), 6)
        self.assertEqual(result["collectionSummary"]["duplicates"], 1)
        self.assertEqual(result["stageCoverage"]["search"]["completed"], 2)

    def test_fair_share_includes_each_selected_platform(self):
        adapters = {"xhs": Adapter(), "douyin": Adapter()}
        class Registry:
            def adapter_for(self, platform):
                return adapters[platform]
        result, _, _ = self.run_pipeline(adapter=Registry(), keywords=["a", "b", "c"],
                                        platforms=["xhs", "douyin"], maxNotes=12)
        counts = Counter((n["platform"], n["source_keyword"]) for n in result["notes"])
        self.assertEqual(set(counts.values()), {2})
        self.assertEqual(len(counts), 6)
        self.assertEqual(result["stageCoverage"]["search"]["completed"], 6)

    def test_small_batches_preserve_all_source_text_and_deduplicate_note_context(self):
        body = "父笔记完整证据" * 1000
        result, runner, _ = self.run_pipeline(adapter=Adapter(comments=11, body=body), maxNotes=1, scanLeads=True)
        signal = [payload for key, payload in runner.calls if key == "lead_signal_agent"]
        qa = [payload for key, payload in runner.calls if key == "critic_qa_agent"]
        self.assertEqual([len(p["leadCandidates"]) for p in signal], [5, 5, 2])
        self.assertEqual([len(p["leads"]) for p in qa], [5, 5, 2])
        for payload in signal:
            self.assertNotIn("comments", payload)
            self.assertEqual(len(payload["noteContexts"]), 1)
            self.assertEqual(payload["noteContexts"][0]["desc"], body)
            for candidate in payload["leadCandidates"]:
                self.assertNotIn("note", candidate)
                self.assertNotIn("sourceRefs", candidate)
                if candidate["lead_type"] == "comment_lead":
                    self.assertTrue(candidate["content"].endswith("完整证据" * 400))
        for payload in qa:
            self.assertNotIn("source_comment", payload["leads"][0])
            self.assertEqual(payload["noteContexts"][0]["desc"], body)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["completed"], 12)
        self.assertEqual(result["stageCoverage"]["qa"]["completed"], 12)
        self.assertEqual(result["stageCoverage"]["report"]["status"], "completed")

    def test_signal_timeout_preserves_prior_batches_and_exposes_pending_downstream(self):
        class FailingRunner(Runner):
            def run(self, key, payload, purpose=""):
                if key == "lead_signal_agent" and sum(k == key for k, _ in self.calls) == 1:
                    raise RuntimeError("模型总预算超时")
                return super().run(key, payload, purpose)
        result, _, snapshots = self.run_pipeline(adapter=Adapter(comments=12), runner=FailingRunner(),
                                                 maxNotes=1, scanLeads=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["leads"]["leadsCount"], 5)
        coverage = result["stageCoverage"]
        self.assertEqual(coverage["lead_signal"]["completed"], 5)
        self.assertEqual(coverage["lead_signal"]["total"], 13)
        self.assertEqual(coverage["lead_signal"]["status"], "partial")
        self.assertIn("超时", coverage["lead_signal"]["error"])
        self.assertEqual(coverage["qa"]["status"], "pending")
        self.assertEqual(coverage["report"]["status"], "pending")
        self.assertEqual(result["collectionSummary"]["failures"][-1]["stage"], "lead_signal")
        self.assertTrue(any(s["stageCoverage"]["lead_signal"]["status"] == "running" for s in snapshots))

    def test_failed_missing_row_repair_keeps_valid_decisions_from_same_batch(self):
        class PartialRunner(Runner):
            def run(self, key, payload, purpose=""):
                if key == "lead_signal_agent" and payload["reviewMode"] == "missing_batch":
                    raise RuntimeError("模型总预算超时")
                output = super().run(key, payload, purpose)
                if key == "lead_signal_agent":
                    output["leads"] = output["leads"][:2]
                return output
        result, _, _ = self.run_pipeline(adapter=Adapter(comments=8), runner=PartialRunner(), maxNotes=1, scanLeads=True)
        self.assertEqual(result["leads"]["leadsCount"], 2)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["completed"], 2)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["total"], 9)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["status"], "partial")

    def test_first_model_failure_marks_actual_stage_failed_not_completed(self):
        class FailingRunner(Runner):
            def run(self, key, payload, purpose=""):
                raise RuntimeError("模型总预算超时")
        result, _, _ = self.run_pipeline(runner=FailingRunner(), maxNotes=2)
        self.assertEqual(result["stageCoverage"]["relevance"]["status"], "failed")
        self.assertEqual(result["stageCoverage"]["relevance"]["completed"], 0)
        self.assertEqual(result["stageCoverage"]["relevance"]["total"], 2)
        # Body evidence is now fetched before the first relevance model call.
        self.assertEqual(result["stageCoverage"]["details"]["status"], "completed")

    def test_qa_failure_keeps_reviewed_count_and_report_pending(self):
        class FailingRunner(Runner):
            def run(self, key, payload, purpose=""):
                if key == "critic_qa_agent" and sum(k == key for k, _ in self.calls) == 1:
                    raise RuntimeError("模型总预算超时")
                return super().run(key, payload, purpose)
        result, _, _ = self.run_pipeline(runner=FailingRunner(), maxNotes=11)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["completed"], 11)
        self.assertEqual(result["stageCoverage"]["qa"]["completed"], 5)
        self.assertEqual(result["stageCoverage"]["qa"]["total"], 11)
        self.assertEqual(result["stageCoverage"]["qa"]["status"], "partial")
        self.assertEqual(result["stageCoverage"]["report"]["status"], "pending")

    def test_disabled_detail_and_comment_stages_are_explicitly_skipped(self):
        result, _, _ = self.run_pipeline(maxNotes=1, readDetail=False, scanLeads=False)
        self.assertEqual(result["stageCoverage"]["details"]["status"], "skipped")
        self.assertEqual(result["stageCoverage"]["comments"]["status"], "skipped")
        self.assertEqual(result["stageCoverage"]["lead_signal"]["total"], 1)

    def test_search_failure_keeps_other_query_results_and_marks_coverage_partial(self):
        class FailingAdapter(Adapter):
            def search(self, keyword, page=1, sort="popular"):
                if keyword == "bad":
                    raise ValueError("fixture unsupported search")
                return super().search(keyword, page, sort)
        result, _, _ = self.run_pipeline(adapter=FailingAdapter(), keywords=["bad", "good"], maxNotes=4)
        self.assertEqual(len(result["notes"]), 4)
        self.assertEqual(result["stageCoverage"]["search"]["status"], "partial")
        self.assertEqual(result["stageCoverage"]["search"]["completed"], 1)
        self.assertEqual(result["stageCoverage"]["search"]["total"], 2)
        self.assertEqual(result["status"], "partial")

    def test_focused_report_prioritizes_qualified_then_uncertain_without_trimming_collection(self):
        class FocusedRunner(Runner):
            def run(self, key, payload, purpose=""):
                output = super().run(key, payload, purpose)
                if key == "lead_signal_agent":
                    for decision, candidate in zip(output["leads"], payload["leadCandidates"]):
                        index = int(candidate["note_id"].rsplit("-", 1)[-1])
                        decision.update(
                            intent_role="buyer" if index >= 28 else "discussion",
                            service_fit="custom_development", demand_origin="self" if index == 29 else "unclear",
                            customization_evidence=["定制智能体"], commissioning_evidence=["找人开发"],
                        )
                return output
        result, runner, _ = self.run_pipeline(
            adapter=Adapter(body="需要定制智能体，找人开发"), runner=FocusedRunner(),
            maxNotes=30, offeringId="agent_custom_development",
        )
        payload = next(payload for key, payload in runner.calls if key == "report_agent")
        self.assertEqual(len(result["notes"]), 30)
        self.assertEqual(len(result["leads"]["leads"]), 30)
        self.assertEqual(len(payload["topLeads"]), 20)
        self.assertTrue(payload["topLeads"][0]["qualified"])
        self.assertTrue(payload["topLeads"][0]["note_id"].endswith("-29"))
        self.assertEqual(payload["topLeads"][1]["classification"], "uncertain")
        self.assertEqual(payload["qualifiedCount"], 1)
        self.assertEqual(payload["classificationCounts"], {"information": 28, "uncertain": 1, "demand": 1})
        self.assertEqual(payload["reportScope"], {"included": 20, "total": 30, "summaryLimit": 20})
        self.assertNotIn("source_comment", payload["topLeads"][0])
        self.assertIn("找人开发", payload["topLeads"][0]["content"])

    def test_focused_gate_and_audit_use_the_same_normalized_decision(self):
        pipeline = HarnessPipeline(Adapter(), {}, runner=Runner())
        plan = normalize_plan({"seedKeywords": ["智能体"], "offeringId": "agent_custom_development",
                               "maxNotes": 1, "maxPagesPerKeyword": 1, "scanLeads": False,
                               "delayMin": 0, "delayMax": 0})
        with patch.object(pipeline, "_save_audit") as audit:
            result = pipeline.run({"confirmedPlan": plan})
        lead = result["leads"]["leads"][0]
        self.assertFalse(lead["qualified"])
        self.assertEqual(lead["classification"], "uncertain")
        self.assertEqual(lead["demand_origin"], "unclear")
        signal_rows = [row for call in audit.call_args_list for row in call.args[1]
                       if row["stage"] == "lead_signal" and row.get("entity_type") in {"note", "comment"}]
        self.assertEqual(signal_rows[0]["decision"], "below_threshold")
        for field in ("intent_role", "service_fit", "demand_origin",
                      "customization_evidence", "commissioning_evidence", "intent_reason"):
            self.assertIn(field, lead)

    def test_focused_qa_rejection_removes_candidate_from_qualified_results(self):
        class RejectedRunner(Runner):
            def run(self, key, payload, purpose=""):
                output = super().run(key, payload, purpose)
                if key == "lead_signal_agent":
                    for decision in output["leads"]:
                        decision.update(intent_role="buyer", service_fit="custom_development",
                                        demand_origin="self", customization_evidence=["定制智能体"],
                                        commissioning_evidence=["找人开发"])
                elif key == "critic_qa_agent":
                    output.update(approved=False, issues=[{
                        "target_id": payload["leads"][0]["lead_id"],
                        "level": "error", "message": "归属需重新核验",
                    }])
                return output
        result, runner, _ = self.run_pipeline(
            adapter=Adapter(body="需要定制智能体，找人开发"), runner=RejectedRunner(),
            maxNotes=1, offeringId="agent_custom_development",
        )
        lead = result["leads"]["leads"][0]
        self.assertEqual(lead["qa_status"], "rejected")
        self.assertFalse(lead["qualified"])
        self.assertEqual(lead["human_review_status"], "pending")
        report = next(payload for key, payload in runner.calls if key == "report_agent")
        self.assertEqual(report["qualifiedCount"], 0)


if __name__ == "__main__":
    unittest.main()
