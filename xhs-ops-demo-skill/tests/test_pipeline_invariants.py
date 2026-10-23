"""Task-level safety and ordering regressions with synthetic adapters only."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from agent_harness.pipeline import HarnessPipeline
from agent_harness.planning import normalize_plan
from douyin_adapter import normalized_douyin_comment
from test_pipeline_coverage import Adapter, Runner, note


SOURCE = "需要定制智能体，找人开发"


class ScopedRunner(Runner):
    def run(self, key, payload, purpose=""):
        output = super().run(key, payload, purpose)
        if key == "lead_signal_agent":
            for row, candidate in zip(output["leads"], payload["leadCandidates"]):
                row.update(intent_role="buyer", service_fit="custom_development", demand_origin="self",
                           customization_evidence=[candidate["content"]], commissioning_evidence=[candidate["content"]])
        return output


class PipelineInvariantTests(unittest.TestCase):
    def execute(self, *, adapter=None, runner=None, offering="general", stop_checker=None, snapshot_callback=None, **options):
        plan = normalize_plan({"seedKeywords": ["智能体"], "keywords": ["智能体"], "maxNotes": 11,
            "maxCommentsTotal": 20, "maxCommentsPerNote": 10, "scanLeads": False,
            "offeringId": offering, "delayMin": 0, "delayMax": 0, **options})
        return HarnessPipeline(adapter or Adapter(body=SOURCE), {}, runner=runner or ScopedRunner()).run(
            {"confirmedPlan": plan}, stop_checker=stop_checker, snapshot_callback=snapshot_callback)

    def assert_qualification_consistent(self, snapshot):
        leads = snapshot["leads"]["leads"]
        self.assertTrue(all(not row["qualified"] for row in leads if row["qa_status"] != "approved"))
        self.assertEqual(snapshot["leads"]["qualifiedCount"], sum(bool(row["qualified"]) for row in leads))

    def test_qa_timeout_never_qualifies_unreviewed_candidates_in_either_mode(self):
        for offering in ("general", "agent_custom_development"):
            for failure_batch in (0, 1):
                with self.subTest(offering=offering, failure_batch=failure_batch):
                    class FailingRunner(ScopedRunner):
                        def run(self, key, payload, purpose=""):
                            if key == "critic_qa_agent" and sum(k == key for k, _ in self.calls) == failure_batch:
                                raise RuntimeError("synthetic QA timeout")
                            return super().run(key, payload, purpose)
                    snapshots = []
                    result = self.execute(runner=FailingRunner(), offering=offering, snapshot_callback=snapshots.append)
                    self.assertEqual(result["status"], "partial")
                    self.assertEqual(result["leads"]["qualifiedCount"], failure_batch * 5)
                    self.assertEqual(result["stageCoverage"]["qa"]["completed"], failure_batch * 5)
                    for snapshot in snapshots:
                        self.assert_qualification_consistent(snapshot)

    def test_signal_failure_and_user_stop_preserve_pending_unqualified_rows(self):
        for stop in (False, True):
            with self.subTest(stop=stop):
                stopped = [False]
                snapshots = []

                def snapshot(value):
                    snapshots.append(value)
                    if stop and value["stageCoverage"]["lead_signal"]["completed"] >= 5:
                        stopped[0] = True

                class FailingRunner(ScopedRunner):
                    def run(self, key, payload, purpose=""):
                        if not stop and key == "lead_signal_agent" and sum(k == key for k, _ in self.calls) == 1:
                            raise RuntimeError("synthetic classification timeout")
                        return super().run(key, payload, purpose)

                result = self.execute(runner=FailingRunner(), stop_checker=lambda: stopped[0], snapshot_callback=snapshot)
                self.assertEqual(result["status"], "stopped" if stop else "partial")
                self.assertEqual(result["leads"]["qualifiedCount"], 0)
                self.assertTrue(result["leads"]["leads"])
                for snapshot in snapshots:
                    self.assert_qualification_consistent(snapshot)

    def test_batch_only_qa_approval_does_not_substitute_for_individual_reviews(self):
        class BatchOnlyRunner(ScopedRunner):
            def run(self, key, payload, purpose=""):
                output = super().run(key, payload, purpose)
                if key == "critic_qa_agent":
                    output.pop("reviews")
                return output

        result = self.execute(runner=BatchOnlyRunner(), maxNotes=1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["leads"]["qualifiedCount"], 0)
        self.assertEqual(result["stageCoverage"]["qa"]["completed"], 0)
        self.assert_qualification_consistent(result)

    def test_missing_platform_comment_ids_never_claim_complete_evidence(self):
        for offering in ("general", "agent_custom_development"):
            for authors, timestamps in ((["alice", "bob"], [100, 100]), (["alice", "alice"], [100, 101]), (["", ""], [None, None])):
                with self.subTest(offering=offering, authors=authors, timestamps=timestamps):
                    class MissingIdAdapter(Adapter):
                        def comments(self, identity, cursor=""):
                            rows = [normalized_douyin_comment({"text": SOURCE, "user": {"sec_uid": author},
                                    "create_time": timestamp}, identity) for author, timestamp in zip(authors, timestamps)]
                            return {"data": {"comments": rows, "has_more": False}}

                    result = self.execute(adapter=MissingIdAdapter(body=SOURCE), offering=offering,
                                          maxNotes=1, scanLeads=True, platforms=["douyin"])
                    comments = [row for row in result["leads"]["leads"] if row["lead_type"] == "comment_lead"]
                    self.assertEqual(result["scannedCommentsCount"], 2)
                    self.assertEqual(len({row["comment_id"] for row in comments}), 2)
                    self.assertEqual([row["user_id"] for row in comments], authors)
                    for row in comments:
                        self.assertTrue(row["id_is_synthetic"])
                        self.assertEqual(row["source_comment_id"], "")
                        self.assertEqual(row["sourceRefs"][0]["commentId"], "")
                        self.assertFalse(row["evidenceComplete"])
                        self.assertFalse(row["preQaQualified"])
                        self.assertFalse(row["qualified"])
                        self.assertEqual(row["qa_status"], "needs_review")

    def test_real_comment_identity_still_deduplicates_repeated_provider_rows(self):
        class RepeatedAdapter(Adapter):
            def comments(self, identity, cursor=""):
                comment = normalized_douyin_comment({"cid": "real-comment", "text": SOURCE,
                    "user": {"sec_uid": "alice"}}, identity)
                return {"data": {"comments": [comment, copy.deepcopy(comment)], "has_more": False}}

        result = self.execute(adapter=RepeatedAdapter(body=SOURCE), maxNotes=1, scanLeads=True, platforms=["douyin"])
        comments = [row for row in result["leads"]["leads"] if row["lead_type"] == "comment_lead"]
        self.assertEqual(result["scannedCommentsCount"], 1)
        self.assertEqual(comments[0]["source_comment_id"], "douyin:real-comment")
        self.assertFalse(comments[0]["id_is_synthetic"])
        self.assertTrue(comments[0]["qualified"])

    def test_metric_orders_retained_notes_and_the_budgeted_comment_scan(self):
        items = [note("liked-first", SOURCE), note("comments-first", SOURCE)]
        items[0]["note_card"]["interact_info"] = {"liked_count": 100, "comment_count": 1}
        items[1]["note_card"]["interact_info"] = {"liked_count": 1, "comment_count": 100}

        class MetricsAdapter(Adapter):
            def __init__(self):
                super().__init__(pages=lambda keyword, page: copy.deepcopy(items))
                self.comment_calls = []

            def read_note(self, identity):
                return {"data": {"items": [copy.deepcopy(next(item for item in items if item["id"] == identity))]}}

            def comments(self, identity, cursor=""):
                self.comment_calls.append(identity)
                return {"data": {"comments": [{"id": "comment-" + identity, "content": SOURCE}], "has_more": False}}

        for metric, expected in (("liked", "liked-first"), ("comments", "comments-first")):
            with self.subTest(metric=metric):
                adapter = MetricsAdapter()
                result = self.execute(adapter=adapter, maxNotes=2, metric=metric, scanLeads=True, maxCommentsTotal=1)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["notes"][0]["id"], expected)
                self.assertEqual(adapter.comment_calls, [expected])


if __name__ == "__main__":
    unittest.main()
