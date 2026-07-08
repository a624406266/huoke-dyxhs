from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_harness.evaluator import HarnessEvaluator
from agent_harness.feedback import FeedbackLearningService
from agent_harness.pipeline import HarnessPipeline
from agent_harness.registry import PromptRegistry
from config_loader import DEFAULT_CONFIG
from llm_client import extract_json_text
from storage import SalesStorage


class FakeAdapter:
    command = ["fake-xhs"]

    def ensure_authenticated(self) -> None:
        return None

    def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        return {
            "data": {
                "items": [
                    {
                        "model_type": "note",
                        "id": "n1",
                        "note_card": {
                            "display_title": "腾讯云服务器续费太贵怎么办",
                            "desc": "企业服务器到期，想找代理优惠和降本方案",
                            "interact_info": {
                                "liked_count": "10",
                                "collected_count": "2",
                                "comment_count": "1",
                                "share_count": "0",
                            },
                            "user": {"nickname": "作者A", "user_id": "author1"},
                            "tag_list": [{"name": "腾讯云"}, {"name": "云服务器"}],
                            "time": int(time.time() * 1000),
                        },
                    },
                    {
                        "model_type": "note",
                        "id": "n2",
                        "note_card": {
                            "display_title": "学生作业整理云计算概念",
                            "desc": "课堂笔记和概念复习，没有采购或咨询语境",
                            "interact_info": {
                                "liked_count": "3",
                                "collected_count": "1",
                                "comment_count": "0",
                                "share_count": "0",
                            },
                            "user": {"nickname": "作者B", "user_id": "author2"},
                            "tag_list": [{"name": "学习笔记"}],
                            "time": int(time.time() * 1000),
                        },
                    }
                ]
            }
        }

    def read_note(self, note_id: str) -> dict[str, Any]:
        return self.search("detail")

    def comments(self, note_id: str, cursor: str = "") -> dict[str, Any]:
        return {
            "data": {
                "comments": [
                    {
                        "id": "c1",
                        "content": "公司服务器到期了，腾讯云续费太贵，有没有优惠报价？",
                        "user_info": {"nickname": "张三", "user_id": "u123456"},
                        "ip_location": "广东",
                    },
                    {
                        "id": "c2",
                        "content": "收藏了，写得很清楚，回头学习一下。",
                        "user_info": {"nickname": "王五", "user_id": "u222"},
                        "ip_location": "上海",
                    }
                ]
            }
        }


class FakeRunner:
    def __init__(self, storage: SalesStorage, task_id: int) -> None:
        self.storage = storage
        self.task_id = task_id
        self.calls: list[str] = []
        self.payloads: dict[str, list[dict[str, Any]]] = {}

    def run(self, agent_key: str, payload: dict[str, Any], *, purpose: str = "") -> dict[str, Any]:
        self.calls.append(agent_key)
        self.payloads.setdefault(agent_key, []).append(payload)
        output = self._output(agent_key, payload)
        self.storage.save_agent_run(
            task_id=self.task_id,
            agent_key=agent_key,
            agent_name=agent_key.replace("_", " ").title(),
            prompt_version=f"{agent_key}-test",
            model="fake-model",
            purpose=purpose,
            input_summary=json.dumps(payload, ensure_ascii=False)[:1000],
            output_summary=json.dumps(output, ensure_ascii=False),
            confidence=float(output.get("confidence", 0.0)),
            error="",
            duration_ms=1,
        )
        return output

    def _output(self, agent_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        base = {
            "decision": "ok",
            "confidence": 0.9,
            "evidence": ["fake evidence"],
            "risks": [],
            "next_actions": [],
        }
        if agent_key == "chief_orchestrator":
            return {**base, "stages": [], "review_policy": {"low_confidence_threshold": 0.6, "high_value_priority": "A", "max_reviews": 8}}
        if agent_key == "planning_agent":
            return {**base, "strategy_summary": "围绕开放需求寻找续费降本线索", "target_customer_profile": "企业云资源负责人", "exclusions": ["学习笔记"], "keywords": ["腾讯云优惠"], "hypotheses": [{"keyword": "腾讯云优惠", "intent": "续费降本", "expected_signals": ["报价", "续费"]}]}
        if agent_key == "relevance_agent":
            if payload.get("reviewMode") == "missing_batch":
                return {**base, "notes": [{"note_id": note.get("note_id"), "decision": "drop", "confidence": 0.8, "evidence": ["补齐判断"], "reason": "补齐后判定不相关"} for note in payload.get("notes") or []]}
            if payload.get("reviewMode") == "single":
                note = payload.get("note") or {}
                return {**base, "notes": [{"note_id": note.get("note_id"), "decision": "keep", "confidence": 0.88, "evidence": ["标题提到腾讯云续费"], "reason": "企业云资源降本相关"}]}
            decisions = []
            for note in payload.get("notes") or []:
                keep = note.get("note_id") == "n1"
                decisions.append({
                    "note_id": note.get("note_id"),
                    "decision": "keep" if keep else "drop",
                    "confidence": 0.88 if keep else 0.91,
                    "evidence": ["标题提到腾讯云续费"] if keep else ["学生作业语境"],
                    "reason": "企业云资源降本相关" if keep else "学习资料，不符合销售需求",
                })
            return {**base, "notes": decisions}
        if agent_key == "lead_signal_agent":
            if payload.get("reviewMode") == "missing_batch":
                return {
                    **base,
                    "leads": [
                        {
                            "candidate_id": item.get("candidate_id"),
                            "lead_type": item.get("lead_type", "comment_lead"),
                            "comment_id": item.get("comment_id"),
                            "note_id": item.get("note_id"),
                            "decision": "ignore",
                            "score": 0,
                            "priority": "D",
                            "stage": "无需求",
                            "confidence": 0.8,
                            "evidence": item.get("content", ""),
                            "demand_summary": "",
                            "reason": "补齐判断后未发现明确采购或咨询意图",
                            "risk": "",
                        }
                        for item in payload.get("leadCandidates") or payload.get("comments") or []
                    ],
                }
            if payload.get("reviewMode") == "single":
                lead = payload["lead"]
                return {**base, "leads": [{**lead, "decision": "lead", "confidence": 0.91, "risk": "需人工核验"}]}
            return {
                **base,
                "leads": [
                    {
                        "comment_id": "c1",
                        "note_id": "n1",
                        "decision": "lead",
                        "score": 90,
                        "priority": "A",
                        "stage": "采购/报价阶段",
                        "confidence": 0.82,
                        "evidence": "公司服务器到期了，腾讯云续费太贵，有没有优惠报价？",
                        "demand_summary": "企业腾讯云服务器续费降本与优惠报价需求",
                        "reason": "明确到期、续费贵、报价",
                        "risk": "公开评论需人工核验语境",
                    },
                    {
                        "comment_id": "c2",
                        "note_id": "n1",
                        "decision": "ignore",
                        "score": 8,
                        "priority": "D",
                        "stage": "无需求",
                        "confidence": 0.94,
                        "evidence": "收藏了，写得很清楚，回头学习一下。",
                        "demand_summary": "",
                        "reason": "收藏学习表达，不包含采购、咨询或联系意图",
                        "risk": "",
                    }
                ],
            }
        if agent_key == "product_strategy_agent":
            return {
                **base,
                "lead_updates": [
                    {
                        "lead_id": "n1:c1",
                        "recommended_products": "腾讯云代理 / 优惠采购",
                        "matched_products": [{"tag": "cloud_agent", "label": "腾讯云代理 / 优惠采购"}],
                        "demand_summary": "疑似需要腾讯云续费降本和代理优惠",
                        "outreach_copy": "你好，看到你提到腾讯云续费成本问题，可以人工帮你先核一下资源和优惠路径。",
                        "reason": "评论明确提到腾讯云续费和报价",
                    }
                ],
                "productDistribution": [{"tag": "cloud_agent", "label": "腾讯云代理 / 优惠采购", "count": 1}],
            }
        if agent_key == "critic_qa_agent":
            return {**base, "approved": True, "issues": [], "review_requests": []}
        if agent_key == "report_agent":
            return {**base, "summary": "识别到 1 条腾讯云续费降本线索。", "insights": ["需求集中在续费成本"], "recommended_actions": ["人工核验后跟进"], "productDistribution": [{"tag": "cloud_agent", "label": "腾讯云代理 / 优惠采购", "count": 1}]}
        if agent_key == "reflection_evolution_agent":
            return {
                **base,
                "task_summary": "Harness 完成端到端多 Agent 扫描。",
                "agent_summaries": [{"agent": "线索识别 Agent", "strengths": "识别明确报价信号", "weaknesses": "", "recommendation": "继续强调证据引用"}],
                "eval_cases": [{"case_type": "lead_signal", "input_excerpt": "续费太贵", "expected_behavior": "识别为报价阶段", "source_id": "c1"}],
                "prompt_improvements": [{"agent_key": "lead_signal_agent", "title": "强调到期续费", "prompt_patch": "当评论同时出现到期、续费贵、报价时应提升置信度。", "expected_gain": "中", "risk": "低"}],
                "evolution_summary": "生成 1 条评估样例和 1 个候选改进。",
            }
        return base


def make_note_item(note_id: str, title: str, desc: str, author: str, comments: int = 10) -> dict[str, Any]:
    return {
        "model_type": "note",
        "id": note_id,
        "note_card": {
            "display_title": title,
            "desc": desc,
            "interact_info": {
                "liked_count": "100",
                "collected_count": "20",
                "comment_count": str(comments),
                "share_count": "5",
            },
            "user": {"nickname": author, "user_id": f"user-{note_id}"},
            "tag_list": [{"name": "AI客服"}],
            "time": int(time.time() * 1000),
        },
    }


class Task3RegressionAdapter:
    command = ["fake-xhs"]

    def ensure_authenticated(self) -> None:
        return None

    def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        return {
            "data": {
                "items": [
                    make_note_item(
                        "n-author",
                        "20W能私有化部署AI客服吗？",
                        "老板给了20w预算，想找人部署多语言AI客服，本地化部署并对接内部系统。",
                        "盐甜笔记",
                        comments=0,
                    ),
                    make_note_item(
                        "n-comment",
                        "深圳AI客服接入企业微信方案",
                        "面向电商和企业微信的AI客服落地方案。",
                        "方案作者",
                        comments=4,
                    ),
                ]
            }
        }

    def read_note(self, note_id: str) -> dict[str, Any]:
        items = self.search("detail")["data"]["items"]
        return {"data": {"items": [item for item in items if item["id"] == note_id]}}

    def comments(self, note_id: str, cursor: str = "") -> dict[str, Any]:
        if note_id != "n-comment":
            return {"data": {"comments": [], "has_more": False, "cursor": ""}}
        if not cursor:
            return {
                "data": {
                    "comments": [
                        {"id": "c-gd", "content": "可以接入企业微信回复客户消息吗？", "user_info": {"nickname": "广东用户", "user_id": "u-gd"}, "ip_location": "广东"},
                        {"id": "c-jx", "content": "有性价比很高的AI客服推荐吗？预算少", "user_info": {"nickname": "江西用户", "user_id": "u-jx"}, "ip_location": "江西"},
                    ],
                    "has_more": True,
                    "cursor": "page2",
                }
            }
        return {
            "data": {
                "comments": [
                    {"id": "c-ignore", "content": "求资料学习一下", "user_info": {"nickname": "学习用户", "user_id": "u-ignore"}, "ip_location": "广东"},
                    {"id": "c-extra", "content": "超过上限不应被采集", "user_info": {"nickname": "多余用户", "user_id": "u-extra"}, "ip_location": "广东"},
                ],
                "has_more": False,
                "cursor": "",
            }
        }


class Task3RegressionRunner(FakeRunner):
    def _output(self, agent_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        base = {
            "decision": "ok",
            "confidence": 0.9,
            "evidence": ["fixture evidence"],
            "risks": [],
            "next_actions": [],
        }
        if agent_key == "chief_orchestrator":
            return {**base, "review_policy": {"low_confidence_threshold": 0.6, "high_value_priority": "A", "max_reviews": 8}}
        if agent_key == "planning_agent":
            return {
                **base,
                "strategy_summary": "找深圳AI客服私有化和企业微信接入线索",
                "target_customer_profile": "深圳企业客服负责人",
                "geo_constraints": {"target_city": "深圳", "target_province": "广东"},
                "exclusions": ["学习资料"],
                "keywords": ["深圳 AI客服 报价"],
                "hypotheses": [{"keyword": "深圳 AI客服 报价", "intent": "报价和落地咨询", "expected_signals": ["预算", "接入"]}],
            }
        if agent_key == "relevance_agent":
            if payload.get("reviewMode") == "missing_batch":
                return {**base, "notes": [{"note_id": note["note_id"], "decision": "keep", "confidence": 0.82, "evidence": ["补齐保留"], "reason": "补齐后仍相关"} for note in payload.get("notes") or []]}
            return {**base, "notes": [{"note_id": "n-author", "decision": "keep", "confidence": 0.95, "evidence": ["20W预算私有化部署"], "reason": "作者本人有明确预算和部署需求"}]}
        if agent_key == "lead_signal_agent":
            if payload.get("reviewMode") == "missing_batch":
                return {
                    **base,
                    "leads": [
                        {
                            "candidate_id": item["candidate_id"],
                            "lead_type": item.get("lead_type", "comment_lead"),
                            "comment_id": item.get("comment_id"),
                            "note_id": item.get("note_id"),
                            "decision": "ignore",
                            "score": 5,
                            "priority": "D",
                            "stage": "无需求",
                            "confidence": 0.86,
                            "evidence": item.get("content", ""),
                            "demand_summary": "",
                            "reason": "只是求资料学习，没有采购、预算或落地咨询。",
                            "risk": "",
                        }
                        for item in payload.get("leadCandidates") or []
                    ],
                }
            return {
                **base,
                "leads": [
                    {
                        "candidate_id": "note:n-author",
                        "lead_type": "note_author",
                        "comment_id": "",
                        "note_id": "n-author",
                        "decision": "lead",
                        "score": 95,
                        "priority": "A",
                        "stage": "预算调研阶段",
                        "confidence": 0.95,
                        "evidence": "20w预算，想找人部署多语言AI客服，本地化部署",
                        "demand_summary": "外贸团队有20W预算，寻找AI客服私有化部署方案。",
                        "reason": "预算、场景和部署方式明确。",
                        "risk": "需确认地域。",
                    },
                    {
                        "candidate_id": "c-gd",
                        "lead_type": "comment_lead",
                        "comment_id": "c-gd",
                        "note_id": "n-comment",
                        "decision": "lead",
                        "score": 80,
                        "priority": "B",
                        "stage": "方案咨询阶段",
                        "confidence": 0.82,
                        "evidence": "可以接入企业微信回复客户消息吗？",
                        "demand_summary": "广东用户咨询AI客服接入企业微信。",
                        "reason": "明确接入场景。",
                        "risk": "",
                    },
                    {
                        "candidate_id": "c-jx",
                        "lead_type": "comment_lead",
                        "comment_id": "c-jx",
                        "note_id": "n-comment",
                        "decision": "lead",
                        "score": 85,
                        "priority": "B",
                        "stage": "工具选型阶段",
                        "confidence": 0.8,
                        "evidence": "有性价比很高的AI客服推荐吗？预算少",
                        "demand_summary": "江西用户寻找低预算AI客服。",
                        "reason": "有预算和工具选型意图。",
                        "risk": "",
                    },
                ],
            }
        if agent_key == "product_strategy_agent":
            if payload.get("reviewMode") == "missing_batch":
                return {
                    **base,
                    "lead_updates": [
                        {
                            "lead_id": lead["lead_id"],
                            "recommended_products": "大模型私有化部署",
                            "matched_products": ["大模型私有化部署"],
                            "demand_summary": lead.get("demand_summary"),
                            "outreach_copy": "看到你在笔记中提到20W预算和AI客服私有化部署，可以先帮你梳理部署边界。",
                            "reason": "作者级预算和私有化部署需求明确。",
                        }
                        for lead in payload.get("leads") or []
                    ],
                    "productDistribution": [],
                }
            return {
                **base,
                "lead_updates": [
                    {
                        "lead_id": "n-comment:c-gd",
                        "recommended_products": "AI 应用开发",
                        "matched_products": ["AI 应用开发"],
                        "demand_summary": "广东用户咨询企业微信AI客服接入。",
                        "outreach_copy": "看到你在咨询企业微信接入AI客服，可以给你一些接口方案参考。",
                        "reason": "企业微信接口接入属于AI应用开发。",
                    }
                ],
                "productDistribution": [],
            }
        if agent_key == "critic_qa_agent":
            return {**base, "approved": True, "issues": [], "review_requests": []}
        if agent_key == "report_agent":
            return {**base, "summary": "识别到作者级和广东评论线索。", "insights": [], "recommended_actions": [], "productDistribution": []}
        if agent_key == "reflection_evolution_agent":
            return {**base, "task_summary": "ok", "agent_summaries": [], "eval_cases": [], "prompt_improvements": [], "evolution_summary": "ok"}
        return base


class AgentHarnessTests(unittest.TestCase):
    def make_storage(self) -> SalesStorage:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SalesStorage(Path(tmp.name) / "sales.db")

    def test_extract_json_text_accepts_markdown_fence(self) -> None:
        self.assertEqual(extract_json_text("```json\n{\"ok\": true}\n```"), "{\"ok\": true}")

    def test_default_user_redaction_is_disabled(self) -> None:
        self.assertFalse(DEFAULT_CONFIG["sales"]["redact_user"])

    def test_sqlite_storage_writes_audit_decisions(self) -> None:
        storage = self.make_storage()
        task_id = storage.create_task({"keyword": "本地 SQLite"})
        storage.save_audit_decisions(task_id, [
            {"stage": "search", "entity_type": "keyword", "decision": "planned", "source_keyword": "本地 SQLite"},
            {"stage": "lead_signal", "entity_type": "comment", "decision": "ignore", "reason": "不含需求", "confidence": 0.9},
        ])
        audit = storage.list_audit_decisions(task_id, limit=10)
        self.assertEqual(audit["total"], 2)
        self.assertEqual([item["decision"] for item in audit["items"]], ["planned", "ignore"])

    def test_prompt_registry_promote_and_disable_rolls_back(self) -> None:
        storage = self.make_storage()
        registry = PromptRegistry(storage)
        registry.ensure_defaults()
        active = storage.get_active_prompt("lead_signal_agent")
        self.assertIsNotNone(active)
        candidate_id = storage.create_prompt_candidate(
            agent_key="lead_signal_agent",
            agent_name="线索识别 Agent",
            base_version=active["version"],
            prompt=active["prompt"] + "\n候选",
            source_task_id=1,
            notes="test",
        )
        promoted = registry.promote(candidate_id)
        self.assertEqual(promoted["id"], candidate_id)
        rolled_back = registry.disable(candidate_id)
        self.assertNotEqual(rolled_back["id"], candidate_id)
        self.assertEqual(rolled_back["status"], "active")

    def test_feedback_storage_creates_learning_record(self) -> None:
        storage = self.make_storage()
        task_id = storage.create_task({"keyword": "腾讯云"}, mode="single")
        storage.save_comment(
            task_id,
            "n1",
            {"id": "c1", "content": "怎么联系，有报价吗？", "user_info": {"nickname": "李四", "user_id": "u789"}},
        )
        storage.save_lead(
            task_id,
            {
                "lead_id": "n1:c1",
                "note_id": "n1",
                "comment_id": "c1",
                "score": 90,
                "priority": "A",
                "demand_summary": "报价咨询",
                "evidence": "怎么联系，有报价吗？",
                "outreach_copy": "人工跟进",
                "feedback": "待人工判断",
            },
        )
        storage.save_feedback("n1:c1", "有效线索")
        feedback_id = storage.create_lead_feedback(
            task_id=task_id,
            lead_id="n1:c1",
            comment_id="c1",
            feedback_type="valid_lead",
            feedback="有效线索",
            user_note="销售确认可跟进",
            evidence="怎么联系，有报价吗？",
            expected_result="类似评论应保持为可跟进线索。",
        )
        item = storage.get_lead_feedback(feedback_id)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["feedbackType"], "valid_lead")
        self.assertEqual(storage.get_lead("n1:c1")["feedback"], "有效线索")

    def test_task_summary_preserves_demand_description(self) -> None:
        storage = self.make_storage()
        task_id = storage.create_task({"demandDescription": "找深圳 AI 客服私有化线索", "keyword": "AI客服"}, mode="single")
        task = storage.get_task(task_id)
        self.assertEqual(task["demandDescription"], "找深圳 AI 客服私有化线索")
        self.assertEqual(task["payload"]["demandDescription"], "找深圳 AI 客服私有化线索")

    def test_feedback_analyze_requires_llm_key(self) -> None:
        storage = self.make_storage()
        task_id = storage.create_task({"keyword": "腾讯云"}, mode="single")
        feedback_id = storage.create_lead_feedback(
            task_id=task_id,
            feedback_type="missed_lead",
            feedback="漏识别线索",
            evidence="怎么联系，怎么卖？",
            expected_result="识别为线索",
        )
        with patch("agent_harness.feedback.has_llm_key", return_value=False):
            with self.assertRaises(RuntimeError):
                FeedbackLearningService({"llm": {"api_key": ""}}, storage).analyze(feedback_id)

    def test_feedback_apply_promotes_runtime_prompt(self) -> None:
        storage = self.make_storage()
        registry = PromptRegistry(storage)
        registry.ensure_defaults()
        task_id = storage.create_task({"keyword": "腾讯云"}, mode="single")
        feedback_id = storage.create_lead_feedback(
            task_id=task_id,
            feedback_type="missed_lead",
            feedback="漏识别线索",
            evidence="怎么联系，怎么卖？",
            expected_result="识别为线索",
        )

        class FakeStructuredRunner:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def run(self, agent_key: str, payload: dict[str, Any], *, purpose: str = "") -> dict[str, Any]:
                assert agent_key == "feedback_learning_agent"
                return {
                    "decision": "应改进线索识别",
                    "confidence": 0.92,
                    "evidence": ["怎么联系，怎么卖？"],
                    "risks": [],
                    "next_actions": ["生成候选 Prompt"],
                    "diagnosis": "联系和购买询问被漏识别。",
                    "target_agents": ["lead_signal_agent"],
                    "prompt_improvements": [
                        {
                            "agent_key": "lead_signal_agent",
                            "title": "强化联系购买信号",
                            "prompt_patch": "当评论出现怎么联系、怎么卖、有报价吗时，结合业务语境识别为高价值销售意图。",
                            "expected_gain": "中",
                            "risk": "低",
                        }
                    ],
                    "eval_case": {
                        "case_type": "missed_lead",
                        "input_excerpt": "怎么联系，怎么卖？",
                        "expected_behavior": "识别为可跟进线索",
                        "source_id": "feedback-test",
                    },
                    "recommended_action": "确认后启用线索识别 Agent 新版本。",
                }

        service = FeedbackLearningService({"llm": {"api_key": "test-key", "api_base": "http://fake/v1", "model": "fake"}}, storage)
        with patch("agent_harness.feedback.StructuredAgentRunner", FakeStructuredRunner):
            analyzed = service.analyze(feedback_id)
        self.assertEqual(analyzed["status"], "analyzed")
        result = service.apply(feedback_id)
        self.assertEqual(result["feedback"]["status"], "applied")
        self.assertEqual(storage.get_active_prompt("lead_signal_agent")["id"], result["feedback"]["candidatePromptId"])
        self.assertIn("怎么联系、怎么卖", storage.get_active_prompt("lead_signal_agent")["prompt"])

    def test_evaluator_auto_promotes_good_candidate(self) -> None:
        storage = self.make_storage()
        registry = PromptRegistry(storage)
        registry.ensure_defaults()
        task_id = storage.create_task({"keyword": "腾讯云"}, mode="single")
        storage.save_agent_run(
            task_id=task_id,
            agent_key="lead_signal_agent",
            agent_name="线索识别 Agent",
            prompt_version="lead-signal-agent-v1",
            model="fake",
            purpose="test",
            input_summary="{}",
            output_summary='{"evidence":["ok"],"confidence":0.9}',
            confidence=0.9,
            error="",
            duration_ms=1,
        )
        active = storage.get_active_prompt("lead_signal_agent")
        candidate_id = storage.create_prompt_candidate(
            agent_key="lead_signal_agent",
            agent_name="线索识别 Agent",
            base_version=active["version"],
            prompt=active["prompt"] + "\n候选",
            source_task_id=task_id,
            notes="test",
        )
        result = HarnessEvaluator(storage).consider_candidate(
            task_id=task_id,
            agent_key="lead_signal_agent",
            candidate_prompt_id=candidate_id,
            active_score=0.82,
            expected_gain=0.06,
            has_safety_risk=False,
        )
        self.assertTrue(result["promoted"])
        self.assertEqual(storage.get_active_prompt("lead_signal_agent")["id"], candidate_id)

    def test_harness_pipeline_runs_with_fake_agents(self) -> None:
        storage = self.make_storage()
        task_id = storage.create_task({"keyword": "腾讯云", "delayMin": 0, "delayMax": 0}, mode="single")
        config = {
            "llm": {"api_key": "test-key", "api_base": "http://fake/v1", "model": "fake-model", "temperature": 0.0},
            "xhs": {"max_count": 20, "read_delay_min": 0, "read_delay_max": 0},
            "sales": {"default_min_score": 60, "default_business_direction": "cloud_ai", "redact_user": False},
        }
        runner = FakeRunner(storage, task_id)
        pipeline = HarnessPipeline(FakeAdapter(), config, storage, runner=runner)
        result = pipeline.run(
            {"demandDescription": "寻找企业腾讯云续费降本和优惠报价需求", "keyword": "腾讯云", "count": 1, "delayMin": 0, "delayMax": 0},
            task_id=task_id,
            event_callback=lambda *args, **kwargs: None,
            progress_callback=lambda *args, **kwargs: None,
        )
        self.assertEqual(result["harness"]["mode"], "multi-agent-llm")
        self.assertEqual(result["demandDescription"], "寻找企业腾讯云续费降本和优惠报价需求")
        self.assertEqual(result["leads"]["leadsCount"], 1)
        self.assertEqual(runner.payloads["chief_orchestrator"][0]["demandDescription"], "寻找企业腾讯云续费降本和优惠报价需求")
        self.assertEqual(runner.payloads["planning_agent"][0]["demandDescription"], "寻找企业腾讯云续费降本和优惠报价需求")
        self.assertGreaterEqual(len(storage.list_agent_runs(task_id)), 7)
        self.assertTrue(result["taskSummary"].get("task_summary"))
        self.assertTrue(result["evolutionSummary"])
        audit = storage.list_audit_decisions(task_id, limit=100)
        decisions = {(item["stage"], item["entityType"], item["entityId"], item["decision"]) for item in audit["items"]}
        self.assertIn(("search", "keyword", "腾讯云优惠", "planned"), decisions)
        self.assertIn(("relevance", "note", "n2", "drop"), decisions)
        self.assertIn(("lead_signal", "comment", "c2", "ignore"), decisions)
        ignored = [item for item in audit["items"] if item["entityId"] == "c2" and item["decision"] == "ignore"][0]
        self.assertIn("收藏学习", ignored["reason"])

    def test_task3_regressions_are_handled_in_pipeline(self) -> None:
        storage = self.make_storage()
        payload = {
            "demandDescription": "找深圳正在做AI客服私有化、想降低客服人力成本、或在评论里询问落地方案的潜在线索",
            "count": 2,
            "minScore": 60,
            "delayMin": 0,
            "delayMax": 0,
        }
        task_id = storage.create_task(payload, mode="single")
        config = {
            "llm": {"api_key": "test-key", "api_base": "http://fake/v1", "model": "fake-model", "temperature": 0.0},
            "xhs": {"max_count": 20, "read_delay_min": 0, "read_delay_max": 0, "max_comments_per_note": 3},
            "sales": {"default_min_score": 60, "default_business_direction": "cloud_ai", "redact_user": False},
        }
        runner = Task3RegressionRunner(storage, task_id)
        result = HarnessPipeline(Task3RegressionAdapter(), config, storage, runner=runner).run(
            payload,
            task_id=task_id,
            event_callback=lambda *args, **kwargs: None,
            progress_callback=lambda *args, **kwargs: None,
        )

        leads = result["leads"]["leads"]
        lead_ids = {lead["lead_id"] for lead in leads}
        self.assertEqual(result["leads"]["totalCommentsScanned"], 3)
        self.assertIn("note:n-author", lead_ids)
        self.assertIn("n-comment:c-gd", lead_ids)
        self.assertNotIn("n-comment:c-jx", lead_ids)

        author = next(lead for lead in leads if lead["lead_id"] == "note:n-author")
        self.assertEqual(author["lead_type"], "note_author")
        self.assertEqual(author["geo_status"], "unknown")
        self.assertEqual(author["recommended_products"], "大模型私有化部署")

        gd = next(lead for lead in leads if lead["lead_id"] == "n-comment:c-gd")
        self.assertEqual(gd["geo_status"], "needs_city_confirmation")
        self.assertIn("深圳", gd["geo_reason"])

        audit = storage.list_audit_decisions(task_id, limit=300)
        items = audit["items"]
        self.assertFalse([item for item in items if item["stage"] == "relevance" and item["decision"] == "missing_decision"])
        self.assertTrue([item for item in items if item["stage"] == "lead_signal" and item["entityId"] == "c-ignore" and item["decision"] == "ignore" and "求资料" in item["reason"]])
        self.assertTrue([item for item in items if item["stage"] == "lead_signal" and item["entityId"] == "c-jx" and item["decision"] == "below_threshold" and "江西" in item["reason"]])
        product_audits = [item for item in items if item["stage"] == "product_strategy" and item["entityType"] == "lead"]
        self.assertEqual({item["entityId"] for item in product_audits}, {"note:n-author", "n-comment:c-gd"})
        self.assertTrue(all(item["decision"] == "updated" for item in product_audits))


if __name__ == "__main__":
    unittest.main()
