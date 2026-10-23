from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
import httpx
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agent_harness.planning import normalize_plan, plan_search
from agent_harness.pipeline import HarnessPipeline
from agent_harness.runtime import StructuredAgentRunner, redact_external
from agent_harness.registry import PromptRegistry
from agent_harness.feedback import FeedbackLearningService
from llm_client import call_llm, call_llm_json, get_last_llm_usage, LLMRequestError
from storage import SalesStorage
from xhs_adapter import XhsAdapter


def plan(**kwargs):
    return normalize_plan({"seedKeywords": ["折叠车"], "keywords": ["折叠车"], "delayMin": 0, "delayMax": 0,
                           "maxNotes": 3, "maxPagesPerKeyword": 2, "maxCommentsPerNote": 2, "maxCommentsTotal": 3, **kwargs})


def note(identity, title="折叠车选购", desc="有适合通勤的推荐吗？"):
    return {"id": identity, "model_type": "note", "note_card": {"title": title, "desc": desc,
            "user": {"user_id": "real-user", "nickname": "真实昵称"}, "interact_info": {}}}


class Adapter:
    command = ["fake"]
    def __init__(self):
        self.calls = []
        self.search_error = None
        self.detail_error = None
        self.auth_error = None
    def ensure_authenticated(self):
        if self.auth_error:
            raise self.auth_error
    def search(self, keyword, page=1, sort="popular"):
        self.calls.append(("search", keyword, page, sort))
        if self.search_error:
            raise self.search_error
        return {"data": {"items": [note(f"{keyword}-{page}-a"), note(f"{keyword}-{page}-b")], "has_more": True}}
    def read_note(self, note_id):
        self.calls.append(("detail", note_id))
        if self.detail_error:
            raise self.detail_error
        return {"data": {"items": [note(note_id)]}}
    def comments(self, note_id, cursor=""):
        self.calls.append(("comments", note_id, cursor))
        return {"data": {"comments": [{"id": f"{note_id}-c{cursor or '1'}", "content": "想买一辆，有报价吗？",
            "user_info": {"user_id": "private-user", "nickname": "隐私昵称"}, "ip_location": "异地"}],
            "has_more": True, "cursor": "2"}}


class Runner:
    def __init__(self):
        self.calls = []
        self.quote = None
    def run(self, key, data, purpose=""):
        self.calls.append((key, copy.deepcopy(data)))
        base = {"decision": "ok", "confidence": .8, "evidence": [], "risks": [], "next_actions": []}
        if key == "relevance_agent":
            return {**base, "notes": [{"note_id": n["note_id"], "decision": "keep", "reason": "相关"} for n in data["notes"]]}
        if key == "lead_signal_agent":
            return {**base, "leads": [{"candidate_id": c["candidate_id"], "decision": "lead", "score": 80,
                "classification": "demand", "evidence": [self.quote or c["content"]], "confidence": .8,
                "demand_summary": "表达购买兴趣", "reason": "原文提问"} for c in data["leadCandidates"]]}
        if key == "critic_qa_agent":
            return {**base, "approved": True, "issues": [], "review_requests": [],
                "reviews": [{"target_id": lead["lead_id"], "status": "approved", "reason": "原文支持"}
                            for lead in data["leads"]]}
        if key == "report_agent":
            return {**base, "summary": "根据实际采集结果生成", "insights": [], "recommended_actions": []}
        raise AssertionError(f"unexpected model call: {key}")


class KeywordPipelineTests(unittest.TestCase):
    def execute(self, options=None, adapter=None, runner=None, **kwargs):
        adapter, runner = adapter or Adapter(), runner or Runner()
        pipeline = HarnessPipeline(adapter, {}, runner=runner)
        return pipeline.run({"confirmedPlan": options or plan(), **kwargs.pop("payload", {})}, **kwargs), adapter, runner

    def test_preview_never_collects_and_failure_is_explicit_manual(self):
        with patch("agent_harness.planning.StructuredAgentRunner") as factory:
            factory.return_value.run.return_value = {"keywords": ["折叠车", "折叠车通勤"], "keywordReasons": {"折叠车通勤": "细化用途"}}
            result = plan_search({}, {"seedKeywords": ["折叠车"]})
            self.assertEqual(result["planningMode"], "llm")
            self.assertTrue(result["requiresConfirmation"])
            self.assertEqual(result["keywords"], ["折叠车", "折叠车通勤"])
            factory.return_value.run.side_effect = RuntimeError("secret")
            result = plan_search({}, {"seedKeywords": ["折叠车"]})
            self.assertEqual(result["planningMode"], "manual")
            self.assertNotIn("secret", result["error"])

    def test_plan_is_neutral_and_custom_limits_are_not_silently_capped(self):
        result = normalize_plan({"keyword": "折叠车", "businessDirection": "cloud_ai", "count": 125,
                                 "maxCommentsPerNote": 1000, "maxCommentsTotal": 20000})
        self.assertEqual(result["maxNotes"], 125)
        self.assertEqual(result["maxCommentsPerNote"], 1000)
        self.assertEqual(result["searchGoal"], "")
        self.assertNotIn("businessDirection", result)
        with self.assertRaises(ValueError):
            normalize_plan({"seedKeywords": ["车"], "maxCommentsTotal": 0})
        with self.assertRaises(ValueError):
            normalize_plan({"seedKeywords": ["车"], "keywords": ["a", "b"], "maxKeywords": 1})

    def test_execution_requires_confirmation_and_ignores_outer_keyword_override(self):
        with self.assertRaisesRegex(ValueError, "确认"):
            HarnessPipeline(Adapter(), {}, runner=Runner()).run({"keyword": "未确认"})
        result, adapter, runner = self.execute(payload={"keyword": "偷偷新增", "maxNotes": 999})
        self.assertEqual(result["status"], "completed")
        self.assertEqual({call[1] for call in adapter.calls if call[0] == "search"}, {"折叠车"})
        self.assertFalse({"chief_orchestrator", "planning_agent", "product_strategy_agent"} & {x[0] for x in runner.calls})
        self.assertNotIn("productDistribution", json.dumps(result))
        self.assertNotIn("云", json.dumps([x[1] for x in runner.calls], ensure_ascii=False))

    def test_global_quotas_pagination_and_unique_comment_cursor(self):
        result, adapter, _ = self.execute(plan(keywords=["词一", "词二"], seedKeywords=["词一"], maxNotes=3,
                                              maxCommentsPerNote=2, maxCommentsTotal=3))
        self.assertEqual(len(result["notes"]), 3)
        self.assertEqual(result["collectionSummary"]["commentsCollected"], 3)
        self.assertEqual(sum(c[0] == "search" for c in adapter.calls), 2)
        counts = {}
        for c in adapter.calls:
            if c[0] == "comments":
                counts[c[1]] = counts.get(c[1], 0) + 1
        self.assertEqual(sorted(counts.values()), [1, 2])

    def test_exclusions_are_applied_to_comments_before_model_classification(self):
        result, _, runner = self.execute(plan(maxNotes=1, excludeKeywords=["报价"]))
        comments = [x for x in result["leads"]["leads"] if x["lead_type"] == "comment_lead"]
        self.assertTrue(comments)
        self.assertTrue(all(x["classification"] == "irrelevant" and not x["qualified"] for x in comments))
        sent = [c for key, data in runner.calls if key == "lead_signal_agent" for c in data["leadCandidates"]]
        self.assertFalse(any("报价" in c["content"] for c in sent))

    def test_same_note_ids_across_platforms_keep_distinct_sources(self):
        adapters = {"xhs": Adapter(), "douyin": Adapter()}
        class Registry:
            def adapter_for(self, name):
                return adapters[name]
        result, _, _ = self.execute(plan(platforms=["xhs", "douyin"], maxPagesPerKeyword=1, maxNotes=4, maxCommentsTotal=8), Registry())
        self.assertEqual({n["platform"] for n in result["notes"]}, {"xhs", "douyin"})
        self.assertEqual(len({x["lead_id"] for x in result["leads"]["leads"]}), len(result["leads"]["leads"]))
        self.assertTrue(any(x["lead_id"].startswith("douyin:") for x in result["leads"]["leads"]))

    def test_remaining_and_seen_ids_apply_across_runs(self):
        result, _, _ = self.execute(payload={"remainingNotes": 1, "remainingCommentsTotal": 1,
            "seenNoteIds": ["xhs:折叠车-1-a"], "seenCommentIds": ["折叠车-1-b-c1"]})
        self.assertEqual([x["id"] for x in result["notes"]], ["折叠车-1-b"])
        self.assertLessEqual(result["collectionSummary"]["commentsCollected"], 1)
        self.assertFalse(any(x["comment_id"] == "折叠车-1-b-c1" for x in result["leads"]["leads"]))

    def test_per_round_page_budget_does_not_mutate_confirmed_plan(self):
        fixed = plan(maxPagesPerKeyword=5, maxNotes=100)
        result, adapter, _ = self.execute(fixed, payload={"pageBudget": 1}, page=3)
        self.assertEqual([x[2] for x in adapter.calls if x[0] == "search"], [3])
        self.assertEqual(result["confirmedPlan"]["maxPagesPerKeyword"], 5)

    def test_stop_after_first_snapshot_preserves_sources_and_skips_model(self):
        snapshots, stopped = [], [False]
        def save(data):
            snapshots.append(data)
            if data["notes"]:
                stopped[0] = True
        result, _, runner = self.execute(stop_checker=lambda: stopped[0], snapshot_callback=save)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["notes"])
        self.assertEqual(runner.calls, [])
        self.assertEqual(snapshots[-1]["status"], "stopped")

    def test_reads_retry_three_total_but_captcha_does_not_retry(self):
        adapter = Adapter()
        adapter.search_error = TimeoutError("timeout")
        with patch.object(HarnessPipeline, "_wait"):
            result, _, _ = self.execute(adapter=adapter)
        self.assertEqual(sum(c[0] == "search" for c in adapter.calls), 3)
        self.assertEqual(result["status"], "partial")
        adapter = Adapter()
        adapter.search_error = RuntimeError("验证码 captcha")
        result, _, runner = self.execute(adapter=adapter)
        self.assertEqual(result["status"], "needs_action")
        self.assertEqual(len(adapter.calls), 1)
        self.assertFalse(runner.calls)

    def test_incomplete_detail_and_invented_quote_cannot_get_approved(self):
        adapter, runner = Adapter(), Runner()
        adapter.detail_error = ValueError("详情不存在")
        runner.quote = "这是模型编造的预算"
        result, _, _ = self.execute(adapter=adapter, runner=runner)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["leads"]["leads"])
        for lead in result["leads"]["leads"]:
            self.assertFalse(lead["evidenceComplete"])
            self.assertEqual(lead["qa_status"], "needs_review")
            self.assertNotIn("编造", lead["evidence"])
            self.assertEqual(lead["human_review_status"], "pending")

    def test_model_and_local_evidence_preserve_business_phone_and_email(self):
        adapter = Adapter()
        def comments(note_id, cursor=""):
            return {"data": {"comments": [{"id": "c", "content": "联系 13812345678 或 buyer@example.com",
                "user_info": {"user_id": "private-id", "nickname": "真实姓名"}}]}}
        adapter.comments = comments
        result, _, runner = self.execute(plan(maxNotes=1), adapter)
        external = json.dumps([x[1] for x in runner.calls], ensure_ascii=False)
        for raw in ["13812345678", "buyer@example.com"]:
            self.assertIn(raw, external)
        # The model receives the source text, not an entire platform user record.
        for raw in ["private-id", "真实姓名", "real-user"]:
            self.assertNotIn(raw, external)
        local = result["leads"]["leads"][0]
        self.assertIn("13812345678", local["content"])
        self.assertTrue(local["evidenceComplete"])
        self.assertEqual(local["sourceRefs"][0]["commentId"], "c")

    def test_five_classifications_are_preserved_even_below_threshold(self):
        class ClassifiedRunner(Runner):
            def run(self, key, data, purpose=""):
                output = super().run(key, data, purpose)
                if key == "lead_signal_agent":
                    for i, item in enumerate(output["leads"]):
                        item.update(classification=["demand", "uncertain", "information", "promotion", "irrelevant"][i % 5],
                                    decision="ignore", score=10)
                return output
        result, _, _ = self.execute(plan(maxNotes=3), runner=ClassifiedRunner())
        self.assertEqual({x["classification"] for x in result["leads"]["leads"]}, {"demand", "uncertain", "information", "promotion", "irrelevant"})
        self.assertEqual(result["leads"]["qualifiedCount"], 0)

    def test_qa_batches_cover_every_candidate_and_keep_per_batch_decisions(self):
        class LargeAdapter(Adapter):
            def search(self, keyword, page=1, sort="popular"):
                return {"data": {"items": [note(f"n-{i}") for i in range(45)]}}
        class ReviewRunner(Runner):
            def run(self, key, data, purpose=""):
                output = super().run(key, data, purpose)
                if key == "critic_qa_agent" and data["leads"][0]["note_id"] == "n-20":
                    output.update(approved=False, issues=[{"target_id": data["leads"][0]["lead_id"],
                                  "level": "error", "message": "该条需核实"}], review_requests=[{"target_id": data["leads"][0]["lead_id"], "reason": "核实第二批", "requested_action": "核验原文"}])
                return output
        result, _, runner = self.execute(plan(maxNotes=45, maxPagesPerKeyword=1, scanLeads=False),
                                         adapter=LargeAdapter(), runner=ReviewRunner())
        batches = [data["leads"] for key, data in runner.calls if key == "critic_qa_agent"]
        self.assertEqual([len(batch) for batch in batches], [5] * 9)
        self.assertEqual(len({lead["lead_id"] for batch in batches for lead in batch}), 45)
        self.assertEqual(result["qa"]["reviewedLeadsCount"], 45)
        self.assertEqual(result["qa"]["batchCount"], 9)
        self.assertFalse(result["qa"]["approved"])
        self.assertEqual(len(result["qa"]["issues"]), 1)
        self.assertEqual([item["reason"] for item in result["qa"]["review_requests"]], ["核实第二批"])
        statuses = {lead["note_id"]: lead["qa_status"] for lead in result["leads"]["leads"]}
        self.assertEqual(statuses["n-0"], "approved")
        self.assertEqual(statuses["n-20"], "rejected")
        self.assertEqual(statuses["n-21"], "approved")  # A neighbour's issue is not this item's error.
        self.assertEqual(statuses["n-44"], "approved")

    def test_qa_interruption_keeps_reviewed_batches_and_unreviewed_pending(self):
        class LargeAdapter(Adapter):
            def search(self, keyword, page=1, sort="popular"):
                return {"data": {"items": [note(f"n-{i}") for i in range(21)]}}
        class FailingReviewRunner(Runner):
            def run(self, key, data, purpose=""):
                if key == "critic_qa_agent" and data["leads"][0]["note_id"] == "n-20":
                    raise RuntimeError("model unavailable")
                return super().run(key, data, purpose)
        snapshots = []
        result, _, _ = self.execute(plan(maxNotes=21, maxPagesPerKeyword=1, scanLeads=False),
            adapter=LargeAdapter(), runner=FailingReviewRunner(), snapshot_callback=snapshots.append)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["qa"]["reviewedLeadsCount"], 20)
        self.assertFalse(result["qa"]["approved"])
        self.assertEqual(result["leads"]["leads"][0]["qa_status"], "approved")
        self.assertEqual(result["leads"]["leads"][-1]["qa_status"], "pending")
        self.assertEqual(snapshots[-1]["status"], "partial")

    def test_flush_writes_only_changed_rows_and_snapshots_remain_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = SalesStorage(Path(folder) / "test.sqlite3")
            task_id = storage.create_task({"keyword": "车"})
            pipeline = HarnessPipeline(Adapter(), {}, storage=storage, runner=Runner())
            snapshots = []
            with patch.object(storage, "save_note", wraps=storage.save_note) as save_note, \
                 patch.object(storage, "save_lead", wraps=storage.save_lead) as save_lead:
                result = pipeline.run({"confirmedPlan": plan()}, task_id=task_id, snapshot_callback=snapshots.append)
                note_writes, lead_writes = save_note.call_count, save_lead.call_count
                pipeline._flush("unchanged", 90)
                pipeline._flush("unchanged", 90)
                self.assertEqual(save_note.call_count, note_writes)
                self.assertEqual(save_lead.call_count, lead_writes)
                old_title = snapshots[-1]["notes"][0]["title"]
                result["notes"][0]["title"] = "经过核实的标题"
                result["leads"]["leads"][0]["reason"] = "补充核实信息"
                self.assertEqual(snapshots[-1]["notes"][0]["title"], old_title)
                pipeline._flush("changed", 90)
                self.assertEqual(save_note.call_count, note_writes + 1)
                self.assertEqual(save_lead.call_count, lead_writes + 1)
                self.assertEqual(snapshots[-1]["notes"][0]["title"], "经过核实的标题")

    def test_xhs_cli_feed_and_html_detail_formats_and_numeric_id(self):
        adapter = object.__new__(XhsAdapter)
        for detail in [
            {"items": [{"id": "123456", "note_card": {"note_id": "123456", "title": "折叠车", "desc": "完整原文",
                           "user": {"user_id": "u"}, "interact_info": {"liked_count": "2"}}}]},
            {"noteId": "123456", "title": "折叠车", "desc": "完整原文", "user": {"userId": "u"},
             "interactInfo": {"likedCount": "2"}},
        ]:
            with self.subTest(detail=detail), patch.object(adapter, "run", return_value={"ok": True, "data": detail}) as command:
                normalized = adapter.read_note("123456")
                command.assert_called_once_with(["read", "https://www.xiaohongshu.com/explore/123456", "--json"], timeout=90)
                item = normalized["data"]["items"][0]
                self.assertEqual(item["model_type"], "note")
                self.assertEqual(item["id"], "123456")
                self.assertEqual(item["note_card"]["user"]["user_id"], "u")
                self.assertEqual(item["note_card"]["interact_info"]["liked_count"], "2")
        comments = {"ok": True, "data": {"comments": [], "cursor": "c2", "has_more": True}}
        with patch.object(adapter, "run", return_value=comments) as command:
            self.assertEqual(adapter.comments("123456", cursor="c1"), comments)
            command.assert_called_once_with(["comments", "https://www.xiaohongshu.com/explore/123456", "--json", "--cursor", "c1"], timeout=90)

    def test_xhs_detail_missing_or_mismatched_identity_never_becomes_evidence(self):
        adapter = object.__new__(XhsAdapter)
        for detail in [{"title": "无来源 ID"}, {"noteId": "other", "title": "错误来源"},
                       {"items": [{"id": "wanted", "note_card": {"note_id": "other", "desc": "错误来源"}}]}]:
            with self.subTest(detail=detail), patch.object(adapter, "run", return_value={"ok": True, "data": detail}):
                with self.assertRaisesRegex(ValueError, "对应笔记 ID"):
                    adapter.read_note("wanted")
        with patch.object(adapter, "run") as command:
            with self.assertRaises(ValueError):
                adapter.read_note("https://www.xiaohongshu.com/explore/n?xsec_token=secret")
            command.assert_not_called()

    def test_llm_three_total_attempts_and_no_auth_retry(self):
        with patch("llm_client.call_llm", side_effect=["[]", '{"x":1}', '{"ok":true}']) as call:
            data, _ = call_llm_json([], required_keys=["ok"], retries=20)
            self.assertTrue(data["ok"])
            self.assertEqual(call.call_count, 3)
        with patch("llm_client.call_llm", side_effect=LLMRequestError("HTTP 401")) as call:
            with self.assertRaises(RuntimeError):
                call_llm_json([])
            self.assertEqual(call.call_count, 1)

    def test_no_default_external_model_and_local_no_key_with_actual_usage(self):
        requests = []
        bodies = [
            {"choices": [{"message": {"content": '{"ok": true}'}}],
             "usage": {"prompt_tokens": 17, "completion_tokens": 6, "total_tokens": 23}},
            {"choices": [{"message": {"content": '{"ok": true}'}}]},
        ]
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=bodies.pop(0))
        client_class = httpx.AsyncClient
        transport = httpx.MockTransport(handle)
        with patch.dict(os.environ, {}, clear=True), patch("llm_client.httpx.AsyncClient",
                side_effect=lambda **kwargs: client_class(transport=transport, **kwargs)) as opened:
            with self.assertRaisesRegex(LLMRequestError, "没有默认"):
                call_llm([])
            opened.assert_not_called()
            call_llm_json([], api_base="http://127.0.0.1:1234/v1", model="local-model")
            self.assertNotIn("Authorization", requests[0].headers)
            self.assertTrue(json.loads(requests[0].content)["stream"])
            usage = get_last_llm_usage()
            self.assertEqual(usage["totalTokens"], 23)
            self.assertTrue(usage["complete"])
            call_llm_json([], api_base="http://localhost:1234/v1", model="local-model")
            self.assertIsNone(get_last_llm_usage()["totalTokens"])
            self.assertFalse(get_last_llm_usage()["available"])

    def test_feedback_only_saves_candidate_does_not_promote(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = SalesStorage(Path(folder) / "test.sqlite3")
            registry = PromptRegistry(storage)
            registry.ensure_defaults()
            task_id = storage.create_task({"keyword": "车"})
            feedback_id = storage.create_lead_feedback(task_id=task_id, feedback_type="missed_lead", evidence="想买")
            storage.save_lead_feedback_analysis(feedback_id, {"prompt_improvements": [{"agent_key": "lead_signal_agent", "prompt_patch": "更完整引用原文"}]}, target_agent_key="lead_signal_agent")
            previous = storage.get_active_prompt("lead_signal_agent")["id"]
            result = FeedbackLearningService({}, storage).apply(feedback_id)
            self.assertFalse(result["promoted"])
            self.assertEqual(storage.get_active_prompt("lead_signal_agent")["id"], previous)
            self.assertNotEqual(result["candidatePromptId"], previous)


if __name__ == "__main__":
    unittest.main()
