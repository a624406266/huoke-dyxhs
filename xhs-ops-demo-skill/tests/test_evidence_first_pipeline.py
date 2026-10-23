"""Evidence-first regressions using injected sources and models, never live services."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agent_harness.pipeline import HarnessPipeline
from agent_harness.planning import normalize_plan


NOW = datetime(2026, 10, 21, 18, tzinfo=timezone(timedelta(hours=8))).timestamp()
BUYER_TEXT = "需要定制门店客服智能体，找人开发，能报价吗？"
SUPPLIER_TEXT = "智能体开发教程：我们提供定制开发服务，展示客服案例。"
UNRELATED_TEXT = "今天记录一份番茄炒蛋食谱，与软件开发无关。"


def source(identity, *, title="今天的一点记录", body="", published=None):
    card = {
        "title": title,
        "desc": body,
        "user": {"user_id": "fixture-author", "nickname": "fixture"},
        "interact_info": {},
    }
    if published is not None:
        card["time"] = published
    return {"id": identity, "model_type": "note", "note_card": card}


class EvidenceAdapter:
    def __init__(self, items, details, comments=None, events=None):
        self.items = items
        self.details = details
        self.comment_rows = comments or {}
        self.events = events if events is not None else []

    def ensure_authenticated(self):
        return None

    def search(self, keyword, page=1, sort="popular"):
        self.events.append(("search", keyword, page))
        return {"data": {"items": copy.deepcopy(self.items), "has_more": False}}

    def read_note(self, identity):
        self.events.append(("detail", identity))
        result = self.details[identity]
        if isinstance(result, Exception):
            raise result
        return {"data": {"items": [copy.deepcopy(result)]}}

    def comments(self, identity, cursor=""):
        self.events.append(("comments", identity))
        rows = [{"id": f"{identity}-comment-{index}", "content": text,
                 "user_info": {"user_id": f"fixture-commenter-{index}"}}
                for index, text in enumerate(self.comment_rows.get(identity, []))]
        return {"data": {"comments": rows, "has_more": False}}


class EvidenceRunner:
    """Title-only input deliberately loses relevance, reproducing task 6's risk."""
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.calls = []

    def run(self, key, data, purpose=""):
        self.events.append(("model", key))
        self.calls.append((key, copy.deepcopy(data)))
        base = {"decision": "complete", "confidence": .9, "evidence": [],
                "risks": [], "next_actions": []}
        if key == "relevance_agent":
            return {**base, "notes": [{"note_id": row["note_id"],
                    "decision": "keep" if "智能体" in (row.get("desc") or "") else "drop",
                    "reason": "正文属于智能体主题" if "智能体" in (row.get("desc") or "") else "未见相关主题",
                    "evidence": [row.get("desc") or row.get("title") or ""]}
                    for row in data["notes"]]}
        if key == "lead_signal_agent":
            rows = []
            for candidate in data["leadCandidates"]:
                demand = BUYER_TEXT in candidate["content"]
                rows.append({"candidate_id": candidate["candidate_id"],
                    "decision": "lead" if demand else "ignore",
                    "classification": "demand" if demand else "information",
                    "score": 90 if demand else 20, "evidence": [candidate["content"]],
                    "demand_summary": "本人询问定制开发报价" if demand else "主题信息",
                    "reason": "按当前说话人的原文判断"})
            return {**base, "leads": rows}
        if key == "critic_qa_agent":
            return {**base, "approved": True, "issues": [], "review_requests": [],
                "reviews": [{"target_id": lead["lead_id"], "status": "approved", "reason": "原文支持"}
                            for lead in data["leads"]]}
        if key == "report_agent":
            return {**base, "summary": "按真实覆盖生成报告", "insights": [], "recommended_actions": []}
        raise AssertionError(f"Unexpected injected model call: {key}")


class EvidenceFirstPipelineTests(unittest.TestCase):
    def run_pipeline(self, items, details, comments=None, *, run_options=None, **options):
        events = []
        adapter = EvidenceAdapter(items, details, comments, events)
        runner = EvidenceRunner(events)
        plan = normalize_plan({"seedKeywords": ["智能体"], "keywords": ["智能体"],
            "offeringId": "general", "searchGoal": "寻找本人提出的智能体定制开发需求",
            "maxNotes": 20, "maxPagesPerKeyword": 1, "maxCommentsPerNote": 10,
            "maxCommentsTotal": 30, "readDetail": True, "scanLeads": True,
            "delayMin": 0, "delayMax": 0, **options})
        with patch("agent_harness.pipeline.time.time", return_value=NOW):
            result = HarnessPipeline(adapter, {}, runner=runner).run({"confirmedPlan": plan}, **(run_options or {}))
        return result, adapter, runner

    def test_plain_title_cannot_hide_a_demand_in_the_body(self):
        result, adapter, runner = self.run_pipeline(
            [source("body-demand")], {"body-demand": source("body-demand", body=BUYER_TEXT)})
        relevance_inputs = [row for key, data in runner.calls if key == "relevance_agent" for row in data["notes"]]
        self.assertEqual([row["desc"] for row in relevance_inputs], [BUYER_TEXT])
        self.assertLess(adapter.events.index(("detail", "body-demand")),
                        adapter.events.index(("model", "relevance_agent")))
        self.assertEqual(result["status"], "completed")
        author = next(row for row in result["leads"]["leads"] if row["lead_type"] == "note_author")
        self.assertTrue(author["qualified"])
        self.assertIn(BUYER_TEXT, author["evidence"])

    def test_all_fifteen_title_only_results_are_read_before_relevance(self):
        identities = [f"task6-source-{index}" for index in range(15)]
        result, adapter, runner = self.run_pipeline(
            [source(identity) for identity in identities],
            {identity: source(identity, body=BUYER_TEXT) for identity in identities},
            maxNotes=15, scanLeads=False)
        detail_events = [event for event in adapter.events if event[0] == "detail"]
        self.assertEqual([event[1] for event in detail_events], identities)
        first_relevance = adapter.events.index(("model", "relevance_agent"))
        self.assertTrue(all(adapter.events.index(event) < first_relevance for event in detail_events))
        relevance_inputs = [row for key, data in runner.calls if key == "relevance_agent" for row in data["notes"]]
        self.assertEqual(len(relevance_inputs), 15)
        self.assertTrue(all(row["desc"] == BUYER_TEXT and row["evidenceComplete"] for row in relevance_inputs))
        self.assertEqual(result["stageCoverage"]["details"]["completed"], 15)
        self.assertEqual(result["leads"]["qualifiedCount"], 15)

    def test_missing_or_failed_body_keeps_comments_and_marks_partial(self):
        for detail in (source("missing-body", title="智能体教程"), ValueError("fixture detail unavailable")):
            with self.subTest(detail_failed=isinstance(detail, Exception)):
                result, adapter, runner = self.run_pipeline(
                    [source("missing-body", title="智能体教程")], {"missing-body": detail},
                    {"missing-body": [BUYER_TEXT]})
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["notes"][0]["relevance"]["decision"], "needs_content")
                self.assertIn(("comments", "missing-body"), adapter.events)
                self.assertEqual(result["leads"]["totalCommentsScanned"], 1)
                comments = [row for row in result["leads"]["leads"] if row["lead_type"] == "comment_lead"]
                self.assertEqual(len(comments), 1)
                self.assertTrue(comments[0]["qualified"])
                relevance_inputs = [row for key, data in runner.calls if key == "relevance_agent" for row in data["notes"]]
                self.assertFalse(any(row["note_id"] == "missing-body" for row in relevance_inputs))
                self.assertTrue(result["collectionSummary"]["failures"])

    def test_complete_unrelated_body_is_valid_zero_with_explained_skips(self):
        result, adapter, _ = self.run_pipeline(
            [source("unrelated")], {"unrelated": source("unrelated", body=UNRELATED_TEXT)})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["leads"]["leadsCount"], 0)
        self.assertEqual(result["collectionSummary"]["failures"], [])
        self.assertNotIn(("comments", "unrelated"), adapter.events)
        self.assertEqual(result["stageCoverage"]["details"]["completed"], 1)
        for name in ("comments", "lead_signal", "qa"):
            stage = result["stageCoverage"][name]
            self.assertEqual(stage["status"], "skipped", name)
            self.assertTrue(stage.get("reason"), name)

    def test_time_verification_detail_is_reused_before_relevance(self):
        result, adapter, runner = self.run_pipeline(
            [source("date-prefetch")],
            {"date-prefetch": source("date-prefetch", body=BUYER_TEXT, published=(NOW - 86400) * 1000)},
            timeFilter="week", scanLeads=False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([event for event in adapter.events if event[0] == "detail"], [("detail", "date-prefetch")])
        self.assertEqual(result["collectionSummary"]["timeResolution"]["attempted"], 1)
        self.assertEqual(result["stageCoverage"]["details"]["completed"], 1)
        relevance = next(data for key, data in runner.calls if key == "relevance_agent")
        self.assertEqual(relevance["notes"][0]["desc"], BUYER_TEXT)

    def test_zero_lead_report_still_receives_source_evidence_and_coverage(self):
        result, _, runner = self.run_pipeline(
            [source("excluded-source")],
            {"excluded-source": source("excluded-source", body=UNRELATED_TEXT)})
        report = next(data for key, data in runner.calls if key == "report_agent")
        self.assertEqual(report["leadsCount"], 0)
        self.assertEqual(report["topLeads"], [])
        self.assertIsInstance(report.get("sourceCoverage"), dict)
        self.assertTrue(report["sourceCoverage"])
        self.assertIsInstance(report.get("sourceEvidence"), list)
        self.assertTrue(report["sourceEvidence"])
        evidence = json.dumps(report["sourceEvidence"], ensure_ascii=False)
        self.assertIn("excluded-source", evidence)
        self.assertIn("番茄炒蛋", evidence)
        self.assertIn("drop", evidence)
        self.assertEqual(result["stageCoverage"]["report"]["completed"], 1)

    def test_detail_reads_remain_within_confirmed_note_budget(self):
        identities = [f"budget-source-{index}" for index in range(9)]
        result, adapter, _ = self.run_pipeline(
            [source(identity) for identity in identities],
            {identity: source(identity, body=BUYER_TEXT) for identity in identities},
            maxNotes=3, scanLeads=False)
        details = [event[1] for event in adapter.events if event[0] == "detail"]
        self.assertEqual(len(details), 3)
        self.assertEqual(set(details), {row["id"] for row in result["notes"]})
        self.assertEqual(result["confirmedPlan"]["maxNotes"], 3)

    def test_supplier_author_does_not_exclude_a_buyer_in_comments(self):
        result, adapter, _ = self.run_pipeline(
            [source("supplier", title="智能体教程")],
            {"supplier": source("supplier", title="智能体教程", body=SUPPLIER_TEXT)},
            {"supplier": [BUYER_TEXT]})
        self.assertEqual(result["status"], "completed")
        self.assertIn(("comments", "supplier"), adapter.events)
        author = next(row for row in result["leads"]["leads"] if row["lead_type"] == "note_author")
        comment = next(row for row in result["leads"]["leads"] if row["lead_type"] == "comment_lead")
        self.assertFalse(author["qualified"])
        self.assertEqual(author["classification"], "information")
        self.assertTrue(comment["qualified"])
        self.assertEqual(comment["evidence"], BUYER_TEXT)

    def test_empty_detail_does_not_relabel_search_snippet_as_full_body(self):
        result, _, runner = self.run_pipeline(
            [source("snippet-only", body=BUYER_TEXT)], {"snippet-only": source("snippet-only")})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["notes"][0]["desc"], "")
        self.assertFalse(result["notes"][0]["evidenceComplete"])
        self.assertEqual(result["notes"][0]["relevance"]["decision"], "needs_content")
        self.assertEqual(result["leads"]["qualifiedCount"], 0)
        relevance_inputs = [row for key, data in runner.calls if key == "relevance_agent" for row in data["notes"]]
        self.assertFalse(relevance_inputs)
        coverage = result["collectionSummary"]["contentCoverage"]
        self.assertEqual(coverage["fetched"], 1)
        self.assertEqual(coverage["complete"], 0)
        self.assertEqual(coverage["missing"], 1)

    def test_user_disabled_detail_reads_remain_summary_only(self):
        result, adapter, runner = self.run_pipeline(
            [source("summary-only", body=BUYER_TEXT)],
            {"summary-only": AssertionError("Disabled details must never be fetched")},
            readDetail=False, scanLeads=False)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(any(event[0] in {"detail", "comments"} for event in adapter.events))
        self.assertEqual(result["stageCoverage"]["details"]["status"], "skipped")
        coverage = result["collectionSummary"]["contentCoverage"]
        self.assertEqual(coverage["mode"], "summary")
        self.assertEqual(coverage["requested"], 0)
        self.assertEqual(coverage["attempted"], 0)
        self.assertFalse(result["notes"][0]["evidenceComplete"])
        report = next(data for key, data in runner.calls if key == "report_agent")
        self.assertEqual(report["sourceCoverage"]["content"]["mode"], "summary")

    def test_stop_during_details_preserves_read_and_pending_sources(self):
        stopped = [False]

        def snapshot(data):
            if data["stageCoverage"]["details"]["completed"] == 1:
                stopped[0] = True

        result, adapter, runner = self.run_pipeline(
            [source("read-first"), source("not-read")],
            {identity: source(identity, body=BUYER_TEXT) for identity in ("read-first", "not-read")},
            run_options={"stop_checker": lambda: stopped[0], "snapshot_callback": snapshot})
        self.assertEqual(result["status"], "stopped")
        self.assertEqual([event for event in adapter.events if event[0] == "detail"], [("detail", "read-first")])
        self.assertFalse(runner.calls)
        self.assertEqual(len(result["notes"]), 2)
        self.assertEqual(result["stageCoverage"]["details"]["status"], "partial")
        coverage = result["collectionSummary"]["contentCoverage"]
        self.assertEqual((coverage["requested"], coverage["attempted"], coverage["complete"], coverage["pending"]), (2, 1, 1, 1))

    def test_captcha_during_detail_stops_before_other_sources_or_models(self):
        result, adapter, runner = self.run_pipeline(
            [source("captcha-source"), source("later-source")],
            {"captcha-source": RuntimeError("验证码 captcha"),
             "later-source": source("later-source", body=BUYER_TEXT)})
        self.assertEqual(result["status"], "needs_action")
        self.assertEqual([event for event in adapter.events if event[0] == "detail"], [("detail", "captcha-source")])
        self.assertFalse(runner.calls)
        self.assertEqual(result["collectionSummary"]["contentCoverage"]["pending"], 1)

    def test_report_discloses_evidence_limit_while_covering_all_sources(self):
        identities = [f"report-source-{index}" for index in range(25)]
        result, _, runner = self.run_pipeline(
            [source(identity) for identity in identities],
            {identity: source(identity, body=UNRELATED_TEXT) for identity in identities},
            maxNotes=25)
        self.assertEqual(result["status"], "completed")
        report = next(data for key, data in runner.calls if key == "report_agent")
        coverage = report["sourceCoverage"]
        self.assertEqual(coverage["notes"], 25)
        self.assertEqual(coverage["relevance"], {"drop": 25})
        self.assertEqual(coverage["sourceEvidenceTotal"], 25)
        self.assertEqual(coverage["sourceEvidenceIncluded"], 20)
        self.assertEqual(coverage["sourceEvidenceLimit"], 20)
        self.assertEqual(len(report["sourceEvidence"]), 20)

    def test_detail_confirmed_out_of_window_is_excluded_not_a_content_gap(self):
        for body in ("", BUYER_TEXT):
            with self.subTest(detail_body_present=bool(body)):
                result, _, runner = self.run_pipeline(
                    [source("older-after-detail", published=NOW - 60)],
                    {"older-after-detail": source("older-after-detail", body=body, published=NOW - 30 * 86400)},
                    timeFilter="week")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["notes"], [])
                self.assertEqual(result["leads"]["leadsCount"], 0)
                details = result["stageCoverage"]["details"]
                self.assertEqual((details["status"], details["completed"], details["total"]), ("completed", 1, 1))
                coverage = result["collectionSummary"]["contentCoverage"]
                self.assertEqual(coverage["requested"], 1)
                self.assertEqual(coverage["complete"], 0)
                self.assertEqual(coverage["excluded"], 1)
                self.assertEqual(coverage["missing"], 0)
                self.assertEqual(coverage["gaps"], [])
                report = next(data for key, data in runner.calls if key == "report_agent")
                self.assertEqual(len(report["sourceEvidence"]), 1)
                excluded = report["sourceEvidence"][0]
                self.assertEqual(excluded["note_id"], "older-after-detail")
                self.assertEqual(excluded["timeFilterCheck"]["status"], "out_of_range")

    def test_empty_comments_are_confirmed_zero_reads_not_unscanned_sources(self):
        identities = ("empty-comments-a", "empty-comments-b")
        result, adapter, runner = self.run_pipeline(
            [source(identity) for identity in identities],
            {identity: source(identity, body=SUPPLIER_TEXT) for identity in identities})
        self.assertEqual(result["status"], "completed")
        self.assertEqual([event[1] for event in adapter.events if event[0] == "comments"], list(identities))
        self.assertEqual(result["leads"]["totalCommentsScanned"], 0)
        coverage = result["stageCoverage"]["comments"]
        self.assertEqual((coverage["status"], coverage["completed"], coverage["total"]), ("completed", 2, 2))
        self.assertEqual((coverage["unscanned"], coverage["limited"]), (0, 0))
        for row in result["notes"]:
            self.assertEqual(row["commentCoverage"]["status"], "completed")
            self.assertEqual(row["commentCoverage"]["requests"], 1)
            self.assertEqual(row["commentCoverage"]["collected"], 0)
        report = next(data for key, data in runner.calls if key == "report_agent")
        self.assertTrue(all(row["commentCoverage"]["status"] == "completed" for row in report["sourceEvidence"]))

    def test_total_comment_budget_discloses_limited_and_unscanned_sources(self):
        identities = ("budget-comments-a", "budget-comments-b")
        result, adapter, runner = self.run_pipeline(
            [source(identity) for identity in identities],
            {identity: source(identity, body=SUPPLIER_TEXT) for identity in identities},
            {identity: [BUYER_TEXT, "另一条买方留言"] for identity in identities},
            maxCommentsTotal=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([event[1] for event in adapter.events if event[0] == "comments"], [identities[0]])
        self.assertEqual(result["leads"]["totalCommentsScanned"], 1)
        coverage = result["stageCoverage"]["comments"]
        self.assertEqual((coverage["completed"], coverage["total"], coverage["limited"], coverage["unscanned"]), (1, 2, 1, 1))
        self.assertTrue(coverage["reason"])
        self.assertEqual(result["notes"][0]["commentCoverage"]["status"], "limited")
        self.assertIn("总上限", result["notes"][0]["commentCoverage"]["reason"])
        report = next(data for key, data in runner.calls if key == "report_agent")
        self.assertEqual(report["sourceCoverage"]["comments"]["unscanned"], 1)

    def test_per_note_comment_limits_are_recorded_without_hiding_other_sources(self):
        identities = ("per-note-a", "per-note-b")
        result, adapter, _ = self.run_pipeline(
            [source(identity) for identity in identities],
            {identity: source(identity, body=SUPPLIER_TEXT) for identity in identities},
            {identity: [BUYER_TEXT, "未纳入的第二条评论"] for identity in identities},
            maxCommentsPerNote=1, maxCommentsTotal=10)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([event[1] for event in adapter.events if event[0] == "comments"], list(identities))
        self.assertEqual(result["leads"]["totalCommentsScanned"], 2)
        coverage = result["stageCoverage"]["comments"]
        self.assertEqual((coverage["completed"], coverage["total"], coverage["limited"], coverage["unscanned"]), (2, 2, 2, 0))
        for row in result["notes"]:
            self.assertEqual(row["commentCoverage"]["status"], "limited")
            self.assertEqual(row["commentCoverage"]["collected"], 1)
            self.assertEqual(row["commentCoverage"]["limit"], 1)
            self.assertIn("单篇", row["commentCoverage"]["reason"])


if __name__ == "__main__":
    unittest.main()
