"""Synthetic model/privacy boundary regressions; no real accounts or network."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from agent_harness.feedback import FeedbackLearningService
from agent_harness.agents import BUSINESS_OUTPUT_CONTRACTS, DEFAULT_AGENT_SPECS
from agent_harness.pipeline import HarnessPipeline
from agent_harness.planning import normalize_plan
from agent_harness.runtime import StructuredAgentRunner, redact_external
from config_loader import DEFAULT_CONFIG
from douyin_adapter import normalized_douyin_comment
from llm_client import LLMRequestError, call_llm, validate_api_base
from storage import SalesStorage
from test_keyword_pipeline import Adapter, Runner, plan


COMMON = {"decision": "ok", "confidence": .8, "evidence": [], "risks": [], "next_actions": []}
REPORT = {**COMMON, "summary": "没有已确认需求。", "insights": [], "recommended_actions": []}
QA = {**COMMON, "approved": True, "issues": [], "reviews": [], "review_requests": []}


class ModelSecurityBoundariesTests(unittest.TestCase):
    def test_feedback_request_and_audit_exclude_stored_identity_and_raw_response(self):
        captured = []

        def fake_model(messages, **kwargs):
            captured.append(json.loads(next(m["content"] for m in messages if m["role"] == "user")))
            return dict(COMMON), json.dumps(COMMON)

        with tempfile.TemporaryDirectory() as temporary:
            storage = SalesStorage(Path(temporary) / "fixture.db")
            task = storage.create_task({"keyword": "合成研究"})
            raw = {"cid": "comment-1", "text": "需要帮助开发一个客服工具。",
                   "user": {"uid": "private-fixture-uid", "sec_uid": "private-fixture-sec",
                            "nickname": "private-fixture-name"}, "extra": "private-fixture-raw"}
            comment = normalized_douyin_comment(raw, "note-1")
            storage.save_comment(task, "note-1", comment)
            storage.save_lead(task, {"lead_id": "lead-1", "note_id": "note-1", "comment_id": comment["id"],
                                    "content": comment["content"], "evidence": comment["content"], "score": 80,
                                    "userId": "private-fixture-lead", "source_comment": comment})
            feedback_id = storage.create_lead_feedback(task_id=task, lead_id="lead-1", comment_id=comment["id"],
                                                       feedback_type="missed_lead", expected_result="保留为待确认需求")
            config = copy.deepcopy(DEFAULT_CONFIG)
            config["llm"].update(api_key="synthetic-key", api_base="https://model.invalid/v1", model="fixture")
            self.assertFalse(config["sales"]["redact_user"])
            with patch("agent_harness.runtime.call_llm_json", fake_model):
                FeedbackLearningService(config, storage).analyze(feedback_id)
            serialized = json.dumps({"requests": captured, "runs": storage.list_agent_runs(task)}, ensure_ascii=False)
            for value in ("private-fixture-uid", "private-fixture-sec", "private-fixture-name",
                          "private-fixture-raw", "private-fixture-lead"):
                self.assertNotIn(value, serialized)
            sent_comment = captured[0]["comment"]
            self.assertEqual(set(sent_comment), {"platform", "commentId", "noteId", "content"})
            self.assertEqual(sent_comment["content"], raw["text"])
            self.assertEqual(captured[0]["feedback"]["expectedResult"], "保留为待确认需求")
            self.assertEqual(storage.get_comment(comment["id"])["payload"]["raw"], raw)

    def test_business_identity_is_preserved_while_credentials_are_removed(self):
        identities = ("userId", "user_id", "sec_user_id", "secUserId", "sec_uid", "uid",
                      "nick_name", "nickName", "profileUrl")
        credentials = ("access_token", "refreshToken", "xsec_token", "api-key", "password", "client_secret")
        value = {"nested": [{**{key: "fixture-contact" for key in identities},
                             **{key: "fixture-credential" for key in credentials}}], "note_id": "source-id"}
        redacted = redact_external(value)
        self.assertNotIn("fixture-credential", json.dumps(redacted))
        for key in identities:
            self.assertEqual(redacted["nested"][0][key], "fixture-contact")
        self.assertEqual(redacted["note_id"], "source-id")

    def test_feedback_uses_original_task_snapshot_after_source_is_collected_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = SalesStorage(Path(temporary) / "fixture.db")
            old_task = storage.create_task({"keyword": "original"}, sales_user_id=7)
            new_task = storage.create_task({"keyword": "later"}, sales_user_id=7)
            for task_id, text in ((old_task, "原任务来源证据"), (new_task, "后续任务改写内容")):
                storage.save_comment(task_id, "note-1", {"id": "comment-1", "content": text})
                storage.save_lead(task_id, {"lead_id": "lead-1", "note_id": "note-1", "comment_id": "comment-1",
                                            "content": text, "evidence": text, "score": 80})
            feedback_id = storage.create_lead_feedback(task_id=old_task, sales_user_id=7, lead_id="lead-1",
                                                       comment_id="comment-1", feedback_type="missed_lead")
            feedback = storage.get_lead_feedback(feedback_id)
            service = FeedbackLearningService({}, storage)
            context = service._analysis_context(feedback)
            self.assertEqual(context["comment"]["content"], "原任务来源证据")
            self.assertEqual(context["lead"]["content"], "原任务来源证据")
            self.assertNotIn("后续任务改写内容", json.dumps(context, ensure_ascii=False))
            wrong_owner = service._analysis_context({**feedback, "salesUserId": 8})
            self.assertEqual(wrong_owner["lead"], {})
            self.assertEqual(wrong_owner["comment"], {})
            without_task = service._analysis_context({**feedback, "taskId": None})
            self.assertEqual(without_task["lead"], {})
            self.assertEqual(without_task["comment"], {})

    def test_remote_http_and_localhost_lookalikes_never_reach_transport(self):
        for base in ("http://model.invalid/v1", "http://localhost.attacker.invalid/v1",
                     "http://127.0.0.1.attacker.invalid/v1", "http://192.168.1.1/v1",
                     "http://[2001:db8::1]/v1", "http://localhost@model.invalid/v1"):
            with self.subTest(base=base), patch("llm_client._await_request") as transport:
                with self.assertRaises(LLMRequestError) as raised:
                    call_llm([], api_key="synthetic-private", api_base=base, model="fixture")
                self.assertEqual(raised.exception.code, "configuration_error")
                self.assertNotIn("synthetic-private", str(raised.exception))
                transport.assert_not_called()

    def test_https_and_literal_loopback_endpoints_are_allowed(self):
        for base in ("https://model.invalid/v1", "http://localhost:5000/v1",
                     "http://127.0.0.1:5000/v1", "http://127.0.0.2/v1", "http://[::1]:5000/v1"):
            with self.subTest(base=base):
                validate_api_base(base)

    def test_missing_report_fields_use_existing_structural_retry(self):
        runner = StructuredAgentRunner({})
        with patch("llm_client.call_llm", side_effect=[json.dumps(COMMON), json.dumps(REPORT)]) as model:
            output = runner.run("report_agent", {})
        self.assertEqual(model.call_count, 2)
        self.assertEqual(output["summary"], REPORT["summary"])

    def test_qa_runtime_contract_explains_required_fields_for_older_prompts(self):
        captured = []

        def fake_model(messages, **kwargs):
            captured.extend(messages)
            self.assertIn("review_requests", kwargs["required_keys"])
            return dict(QA), json.dumps(QA)

        StructuredAgentRunner({}, llm_json_callable=fake_model).run("critic_qa_agent", {})
        contracts = [m["content"] for m in captured if m["role"] == "system"]
        self.assertTrue(any("error/warning/info" in value and "review_requests" in value for value in contracts))

    def test_report_human_language_contract_reaches_old_active_prompts_without_rewrite_calls(self):
        report = {**REPORT, "summary": "围绕 OpenAI Agent 定制进行了本次检索，尚无已确认需求。",
                  "evidence": ['用户原话："Need a Claude workflow"；来源 https://example.com/request'],
                  "insights": ["没有评论记录，不能据此断言没有需求。"],
                  "recommended_actions": ["继续核验用户的 GitHub Copilot 需求。"]}
        old = {"agentKey": "report_agent", "version": "report-agent-keyword-v3", "status": "active",
               "prompt": "旧报告说明：逐条列出 topLeads、qualifiedCount、visited=true。"}
        contract = BUSINESS_OUTPUT_CONTRACTS["report_agent"]
        for offering in ("general", "agent_custom_development"):
            with self.subTest(offering=offering):
                runner = StructuredAgentRunner({"llm": {"model": "synthetic"}})
                with patch.object(runner.registry, "active_prompt", return_value=old), \
                        patch("llm_client.call_llm", return_value=json.dumps(report)) as model:
                    output = runner.run("report_agent", {"plan": {"offeringId": offering,
                        "keywords": ["OpenAI Agent 定制"]}, "qualifiedCount": 0, "topLeads": []})
                self.assertEqual(model.call_count, 1)
                messages = model.call_args.args[0]
                self.assertEqual(messages[0]["content"], old["prompt"])
                self.assertIn({"role": "system", "content": contract}, messages)
                self.assertEqual(output, report)  # Do not rewrite keywords, quotations or links.
                self.assertIn('"qualifiedCount":0', next(m["content"] for m in messages if m["role"] == "user"))

    def test_report_contract_preserves_machine_schema_and_covers_human_fields(self):
        contract = BUSINESS_OUTPUT_CONTRACTS["report_agent"]
        self.assertIn(contract, DEFAULT_AGENT_SPECS["report_agent"].system_prompt)
        self.assertEqual(set(DEFAULT_AGENT_SPECS["report_agent"].required_keys), {
            "decision", "confidence", "evidence", "risks", "next_actions", "summary", "insights", "recommended_actions"})
        for wording in ("自然中文", "供人阅读", "内部字段名", "JSON 片段", "英文关键词", "品牌", "逐字引文", "URL", "不得翻译结构键"):
            self.assertIn(wording, contract)

    def test_malformed_qa_and_report_outputs_are_rejected(self):
        invalid = [("report_agent", COMMON), ("report_agent", {**REPORT, "insights": "wrong type"}),
                   ("critic_qa_agent", COMMON), ("critic_qa_agent", {**QA, "approved": "true"}),
                   ("critic_qa_agent", {key: value for key, value in QA.items() if key != "reviews"}),
                   ("critic_qa_agent", {key: value for key, value in QA.items() if key != "review_requests"}),
                   ("critic_qa_agent", {**QA, "review_requests": [42]}),
                   ("critic_qa_agent", {**QA, "review_requests": [""]}),
                   ("critic_qa_agent", {**QA, "review_requests": ["review without target"]}),
                   ("critic_qa_agent", {**QA, "issues": ["not an issue object"]}),
                   ("critic_qa_agent", {**QA, "issues": [{"level": "error", "target_id": "lead-1", "message": "错误"}]})]
        for agent_key, output in invalid:
            with self.subTest(agent=agent_key, output=output):
                runner = StructuredAgentRunner({}, llm_json_callable=lambda *a, **kw: (output, json.dumps(output)))
                with self.assertRaises(ValueError):
                    runner.run(agent_key, {})

    def test_invalid_qa_or_report_cannot_mark_pipeline_completed(self):
        for invalid_stage, stage in (("critic_qa_agent", "qa"), ("report_agent", "report")):
            class IncompleteRunner(Runner):
                def run(self, key, data, purpose=""):
                    return dict(COMMON) if key == invalid_stage else super().run(key, data, purpose)
            with self.subTest(stage=stage):
                result = HarnessPipeline(Adapter(), {}, runner=IncompleteRunner()).run(
                    {"confirmedPlan": plan(maxNotes=1, scanLeads=False)})
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["stageCoverage"][stage]["status"], "failed")
                self.assertEqual(result["stageCoverage"][stage]["completed"], 0)
                self.assertEqual(result["report"], {})
                self.assertTrue(result["collectionSummary"]["failures"])

    def test_douyin_pagination_is_rejected_before_collection(self):
        for platforms in (["douyin"], ["xhs", "douyin"]):
            payload = {"seedKeywords": ["合成研究"], "platforms": platforms, "maxPagesPerKeyword": 2}
            with self.subTest(platforms=platforms):
                with self.assertRaisesRegex(ValueError, "抖音.*分页"):
                    normalize_plan(payload)
                adapter = Adapter()
                with self.assertRaisesRegex(ValueError, "抖音.*分页"):
                    HarnessPipeline(adapter, {}, runner=Runner()).run({"confirmedPlan": payload})
                self.assertEqual(adapter.calls, [])
        self.assertEqual(normalize_plan({"seedKeywords": ["合成研究"], "platforms": ["xhs"],
                                         "maxPagesPerKeyword": 2})["maxPagesPerKeyword"], 2)


if __name__ == "__main__":
    unittest.main()
