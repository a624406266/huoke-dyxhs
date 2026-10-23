from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_harness.pipeline import HarnessPipeline
from douyin_adapter import DouyinBrowserActionAdapter, DouyinReadAdapter, normalized_douyin_comment, normalized_douyin_post
from storage import SalesStorage


class FakeDouyinReadAdapter(DouyinReadAdapter):
    def __init__(self) -> None:
        self.config = {"socialdatax": {"api_key_env": "SOCIALDATAX_API_KEY"}}
        self.root = ROOT
        self.command = ["fake-opencli"]
        self.calls: list[list[str]] = []

    def ensure_authenticated(self) -> None:
        return None

    def run(self, args: list[str], timeout: int = 90) -> Any:
        self.calls.append(args)
        if args[0] == "douyin-search":
            return {
                "data": {
                    "works": [
                        {
                            "aweme_id": "aweme-1",
                            "desc": "AI 客服报价怎么做",
                            "author": {"nickname": "抖音作者", "sec_uid": "sec-author"},
                            "statistics": {"digg_count": "1.2万", "comment_count": 3, "share_count": 4},
                            "create_time": int(time.time()),
                            "share_url": "https://www.douyin.com/video/aweme-1",
                        }
                    ]
                }
            }
        if args[0] == "douyin-comments":
            return {
                "data": {
                    "comments": [
                        {
                            "cid": "comment-1",
                            "text": "有报价表吗，想接入企业微信客服",
                            "user": {"nickname": "抖音用户", "sec_uid": "sec-user-1"},
                            "ip_label": "广东",
                        }
                    ],
                    "has_more": False,
                }
            }
        if args[0] == "douyin-detail":
            return {
                "aweme_id": "aweme-1",
                "desc": "AI 客服报价怎么做",
                "author": {"nickname": "抖音作者", "sec_uid": "sec-author"},
                "statistics": {"digg_count": 12000, "comment_count": 3},
            }
        return {}


class FakePlatformRegistry:
    command = ["fake-registry"]

    def __init__(self, douyin: Any) -> None:
        self.douyin = douyin

    def adapter_for(self, platform: str) -> Any:
        if platform != "douyin":
            raise AssertionError(f"unexpected platform: {platform}")
        return self.douyin


class DouyinRunner:
    def __init__(self, storage: SalesStorage, task_id: int) -> None:
        self.storage = storage
        self.task_id = task_id

    def run(self, agent_key: str, payload: dict[str, Any], *, purpose: str = "") -> dict[str, Any]:
        base = {"decision": "ok", "confidence": 0.9, "evidence": [], "risks": [], "next_actions": []}
        if agent_key == "chief_orchestrator":
            return {**base, "review_policy": {"low_confidence_threshold": 0.6, "high_value_priority": "A", "max_reviews": 8}}
        if agent_key == "planning_agent":
            return {**base, "keywords": ["AI客服 报价"], "strategy_summary": "找抖音评论里的报价线索"}
        if agent_key == "relevance_agent":
            return {**base, "notes": [{"note_id": item["note_id"], "decision": "keep", "confidence": 0.9, "reason": "相关", "evidence": ["报价"]} for item in payload.get("notes") or []]}
        if agent_key == "lead_signal_agent":
            if payload.get("reviewMode") == "single":
                return {**base, "leads": [{**payload["lead"], "decision": "lead", "confidence": 0.91}]}
            return {
                **base,
                "leads": [
                    {
                        "candidate_id": item["candidate_id"],
                        "lead_type": item.get("lead_type", "comment_lead"),
                        "comment_id": item.get("comment_id"),
                        "note_id": item.get("note_id"),
                        "decision": "lead",
                        "score": 86,
                        "priority": "A",
                        "stage": "报价咨询阶段",
                        "confidence": 0.88,
                        "evidence": item.get("content", ""),
                        "demand_summary": "抖音用户咨询 AI 客服报价和企业微信接入。",
                        "reason": "明确询问报价和接入",
                        "risk": "",
                    }
                    for item in payload.get("leadCandidates") or []
                ],
            }
        if agent_key == "product_strategy_agent":
            return {
                **base,
                "lead_updates": [
                    {
                        "lead_id": lead["lead_id"],
                        "recommended_products": "AI 客服集成",
                        "matched_products": ["AI 客服集成"],
                        "demand_summary": lead.get("demand_summary"),
                        "outreach_copy": "你好，看到你在问 AI 客服报价，可以先帮你梳理接入范围。",
                        "reason": "评论提到报价和企业微信",
                    }
                    for lead in payload.get("leads") or []
                ],
                "productDistribution": [],
            }
        if agent_key == "critic_qa_agent":
            return {**base, "approved": True, "issues": [], "review_requests": [],
                "reviews": [{"target_id": lead["lead_id"], "status": "approved", "reason": "原文支持"}
                            for lead in payload["leads"]]}
        if agent_key == "report_agent":
            return {**base, "summary": "识别到抖音评论线索。", "insights": [], "recommended_actions": [], "productDistribution": []}
        if agent_key == "reflection_evolution_agent":
            return {**base, "task_summary": "ok", "agent_summaries": [], "eval_cases": [], "prompt_improvements": [], "evolution_summary": "ok"}
        return base


class FakeActionHelper:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.confirmed = False
        self.calls: list[list[str]] = []

    def confirm(self) -> dict[str, Any]:
        self.confirmed = True
        return {"ok": True}

    def _run_script(self, script: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
        self.calls.append(args)
        return self.responses.pop(0)


class DouyinPlatformTests(unittest.TestCase):
    def make_storage(self) -> SalesStorage:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SalesStorage(Path(tmp.name) / "sales.db")

    def test_douyin_read_adapter_normalizes_search_and_comments(self) -> None:
        adapter = FakeDouyinReadAdapter()
        search = adapter.search("AI客服")
        post = search["data"]["items"][0]
        self.assertEqual(post["platform"], "douyin")
        self.assertEqual(post["id"], "aweme-1")
        self.assertEqual(post["note_card"]["user"]["sec_user_id"], "sec-author")
        comments = adapter.comments("aweme-1")
        comment = comments["data"]["comments"][0]
        self.assertEqual(comment["platform"], "douyin")
        self.assertEqual(comment["id"], "douyin:comment-1")
        self.assertEqual(comment["user_info"]["sec_user_id"], "sec-user-1")

    def test_douyin_missing_comment_id_remains_missing(self) -> None:
        comment = normalized_douyin_comment({"text": "想了解报价", "user": {"nickname": "用户"}}, "aweme-1")
        self.assertIsNotNone(comment)
        self.assertEqual(comment["id"], "")
        self.assertEqual(comment["raw_comment_id"], "")
        self.assertEqual(comment["source_comment_id"], "")
        self.assertTrue(comment["id_is_synthetic"])

    def test_douyin_search_rejects_unimplemented_pages_before_provider_call(self) -> None:
        adapter = FakeDouyinReadAdapter()
        adapter.search("AI客服", page=1)
        first_calls = list(adapter.calls)
        for page in (2, 0, -1, True, "1"):
            with self.subTest(page=page), self.assertRaisesRegex(ValueError, "尚未支持分页"):
                adapter.search("AI客服", page=page)
        self.assertEqual(adapter.calls, first_calls, "unsupported pages must not repeat a paid first-page request")

    def test_douyin_socialdatax_flat_fields_are_normalized(self) -> None:
        adapter = FakeDouyinReadAdapter()
        post = adapter.search("AI客服")["data"]["items"][0]
        flat = normalized_douyin_post(
            {
                "aweme_id": "aweme-flat",
                "description": "企业 AI 内训方案",
                "author_name": "抖音作者",
                "author_user_id": "author-flat",
                "author_sec_user_id": "sec-author-flat",
                "like_count": 16185,
                "comment_count": 2229,
                "collect_count": 11632,
                "share_count": 3725,
                "topic_tags": '[{"name":"企业AI"}]',
                "publish_time": int(time.time()),
            },
            1,
        )
        self.assertIsNotNone(flat)
        self.assertEqual(post["note_card"]["interact_info"]["liked_count"], 12000)
        self.assertEqual(flat["note_card"]["display_title"], "企业 AI 内训方案")
        self.assertEqual(flat["note_card"]["interact_info"]["liked_count"], 16185)
        self.assertEqual(flat["note_card"]["interact_info"]["comment_count"], 2229)
        self.assertEqual(flat["note_card"]["user"]["sec_user_id"], "sec-author-flat")
        self.assertEqual(flat["note_card"]["tag_list"][0]["name"], "企业AI")

    def test_douyin_browser_action_adapter_delegates_verified_contract(self) -> None:
        helper = FakeActionHelper([])
        adapter = DouyinBrowserActionAdapter(helper)  # type: ignore[arg-type]
        adapter._browser = Mock()
        identity = {"identity": "douyin:account-fixture"}
        adapter._browser.account_identity.return_value = identity
        action = {"action": "send_dm", "accountIdentity": identity["identity"], "targetUserId": "sec-user", "text": "你好"}
        result = {"status": "unknown", "submitted": True, "error": "没有可核验回执", "evidence": {}}
        adapter._browser.execute.return_value = result
        adapter.ensure_authenticated()
        self.assertTrue(helper.confirmed)
        self.assertEqual(adapter.account_identity(), identity)
        self.assertEqual(adapter.execute(action), result)
        adapter._browser.execute.assert_called_once_with(action)

    def test_pipeline_runs_douyin_only_and_prefixes_douyin_lead_ids(self) -> None:
        storage = self.make_storage()
        task_id = storage.create_task({"demandDescription": "找抖音 AI 客服报价线索", "platforms": ["douyin"]})
        adapter = FakeDouyinReadAdapter()
        config = {
            "llm": {"api_key": "test-key", "api_base": "http://fake/v1", "model": "fake"},
            "platforms": {"enabled": ["douyin"]},
            "xhs": {"max_count": 20, "read_delay_min": 0, "read_delay_max": 0},
            "douyin": {"max_comments_per_post": 5},
            "sales": {"default_min_score": 60, "default_business_direction": "cloud_ai", "redact_user": False},
        }
        result = HarnessPipeline(FakePlatformRegistry(adapter), config, storage, runner=DouyinRunner(storage, task_id)).run(
            {"confirmedPlan": {"seedKeywords": ["AI客服 报价"], "keywords": ["AI客服 报价"],
                "searchGoal": "找抖音 AI 客服报价线索", "platforms": ["douyin"], "delayMin": 0, "delayMax": 0}},
            task_id=task_id,
            event_callback=lambda *args, **kwargs: None,
            progress_callback=lambda *args, **kwargs: None,
        )
        lead = result["leads"]["leads"][0]
        self.assertEqual(result["platforms"], ["douyin"])
        self.assertEqual(lead["platform"], "douyin")
        self.assertTrue(lead["lead_id"].startswith("douyin:aweme-1:"))
        self.assertEqual(lead["target_user_id"], "sec-user-1")
        self.assertEqual(storage.get_lead(lead["lead_id"])["platform"], "douyin")
        relevance_audit = storage.list_audit_decisions(task_id, stage="relevance", platform="douyin")
        self.assertGreater(relevance_audit["total"], 0)
        self.assertEqual(storage.list_audit_decisions(task_id, stage="relevance", platform="xhs")["total"], 0)


if __name__ == "__main__":
    unittest.main()
