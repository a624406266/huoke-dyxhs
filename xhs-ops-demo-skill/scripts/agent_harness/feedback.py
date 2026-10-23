from __future__ import annotations

import copy
import json
import re
import threading
from typing import Any

from llm_client import has_llm_key
from workbench_store import WorkbenchStore, digest

from .registry import LEARNING_AGENTS, PromptRegistry
from .runtime import StructuredAgentRunner, normalize_reflection_output, redact_external
from .offering import GENERAL, normalize_offering


LEARNING_POLICY = (
    "先比较原始模型输出、后处理结果、QA和人工反馈。cause_layer 只能为 prompt/rule/qa/feedback/collection/unknown。"
    "规则覆盖正确输出、批次QA状态污染、采集缺失和相互冲突的人工标签必须单列工程/人工复核事项，"
    "不能通过修改 Prompt 掩盖。prompt_improvements 每项需 agent_key、title、prompt_patch、cause_layer、"
    "evidence_ids（本次输入中对应候选或来源编号）；只有已确认是 prompt 层的问题才建议候选。"
    "复盘是建议，不是质量验收；不得编造评测、通过率或真实使用数据。自动 eval_cases 仅供人工标注开发集，"
    "不得成为冻结验收标签。保持原任务 confirmedPlan/offeringId/searchGoal，父笔记不能充当评论者证据。"
)
LEAD_FIELDS = ("platform", "lead_id", "lead_type", "note_id", "comment_id", "content", "evidence",
               "evidenceComplete", "classification", "qualified", "score", "confidence", "demand_summary",
               "reason", "risk", "intent_role", "service_fit", "demand_origin", "customization_evidence",
               "commissioning_evidence", "intent_reason", "qa_status", "qa_issues", "raw_classification",
               "raw_reason", "normalization_changes", "original_model_decision", "business_review_required",
               "preQaQualified", "qa_reason", "qualification_reason")
_LEARNING_LOCK = threading.RLock()


def _select(row: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in fields if row and key in row}


FEEDBACK_TYPE_LABELS = {
    "valid_lead": "有效线索",
    "invalid_lead": "误判线索",
    "missed_lead": "漏识别线索",
    "wrong_product": "历史产品反馈",
    "irrelevant": "内容不相关",
    "wrong_evidence": "证据不支持判断",
    "wrong_classification": "分类不准确",
    "bad_outreach": "跟进话术不合适",
    "other": "其他",
}


class FeedbackLearningService:
    def __init__(self, config: dict[str, Any], storage: Any) -> None:
        self.config = config
        self.storage = storage
        self.registry = PromptRegistry(storage)

    def list_feedback(self, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"feedback": self.storage.list_lead_feedback(status=status, limit=limit)}

    def analyze(self, feedback_id: int) -> dict[str, Any]:
        llm_config = self.config.get("llm") or {}
        if not has_llm_key(llm_config.get("api_key"), llm_config.get("api_base")):
            raise RuntimeError("反馈学习必须配置 LLM API Key；请先在设置中填写 API Key 或设置 LLM_API_KEY/OPENAI_API_KEY。")
        feedback = self._feedback(feedback_id)
        self.registry.ensure_defaults()
        context = self._analysis_context(feedback)
        runner = StructuredAgentRunner(
            self.config,
            self.storage,
            int(feedback.get("taskId") or 0),
            self.registry,
            run_id=f"feedback-{feedback_id}-{digest(context)[:16]}", run_type="feedback",
        )
        analysis = runner.run(
            "feedback_learning_agent",
            context,
            purpose="人工反馈学习分析",
        )
        analysis = self._normalize_analysis(feedback, analysis)
        analysis["learningContext"] = context["learningContext"]
        analysis["causalFindings"] = context["causalFindings"]
        target_agent_key = (analysis.get("target_agents") or [""])[0]
        self.storage.save_lead_feedback_analysis(feedback_id, analysis, target_agent_key=target_agent_key)
        return self._feedback(feedback_id)

    def apply(self, feedback_id: int) -> dict[str, Any]:
        with _LEARNING_LOCK:
            return self._apply(feedback_id)

    def _apply(self, feedback_id: int) -> dict[str, Any]:
        feedback = self._feedback(feedback_id)
        if feedback.get("candidatePromptId"):
            return {"feedback": feedback, "candidatePromptId": feedback["candidatePromptId"],
                    "promoted": False, "requiresEvaluation": True,
                    "message": "已有候选；重复请求未新增或启用版本。"}
        analysis = feedback.get("analysis") or {}
        if not analysis:
            raise ValueError("该反馈尚未完成 AI 分析，不能应用。")
        improvements = analysis.get("prompt_improvements") or []
        if not improvements:
            raise ValueError("AI 分析没有给出可应用的 Prompt/Skill 改进。")
        item = self._first_supported_improvement(improvements)
        agent_key = str(item.get("agent_key") or "").strip()
        prompt_patch = str(item.get("prompt_patch") or "").strip()
        if not agent_key or not prompt_patch:
            raise ValueError("Prompt/Skill 改进缺少 agent_key 或 prompt_patch。")
        if analysis.get("causalFindings") or item.get("cause_layer", "prompt") != "prompt":
            raise ValueError("该反馈涉及规则、QA或证据冲突，须先人工处理根因，不能用 Prompt 候选覆盖。")
        context = analysis.get("learningContext") or self._analysis_context(feedback)["learningContext"]
        candidate_id = self.registry.create_learning_candidate(
            agent_key, prompt_patch, context=context, source_key=f"feedback:{feedback_id}",
            source_task_id=int(feedback.get("taskId") or 0), title=str(item.get("title") or "人工反馈学习改进"))
        eval_case = analysis.get("eval_case") or analysis.get("evalCase") or {}
        if eval_case:
            self.storage.save_eval_case(int(feedback.get("taskId") or 0), {
                **eval_case, "learningContext": context, "source": "feedback_suggestion",
                "humanVerified": False, "eligibleForAcceptance": False})
        self.storage.mark_lead_feedback_applied(feedback_id, candidate_id, agent_key)
        return {
            "feedback": self._feedback(feedback_id),
            "activePrompt": self.storage.get_active_prompt(agent_key),
            "candidatePromptId": candidate_id,
            "promoted": False,
            "requiresEvaluation": True,
            "evaluationStatus": "awaiting_frozen_dataset",
            "message": "候选已保存；同服务模式的人工冻结集未评测验收前不生效。",
        }

    def dismiss(self, feedback_id: int) -> dict[str, Any]:
        self.storage.dismiss_lead_feedback(feedback_id)
        return self._feedback(feedback_id)

    def _feedback(self, feedback_id: int) -> dict[str, Any]:
        feedback = self.storage.get_lead_feedback(feedback_id)
        if not feedback:
            raise ValueError(f"反馈 {feedback_id} 不存在。")
        return feedback

    def _analysis_context(self, feedback: dict[str, Any]) -> dict[str, Any]:
        task_id = feedback.get("taskId")
        scope = {"sales_user_id": feedback.get("salesUserId"), "task_id": task_id}
        # Missing task snapshots must not silently borrow a later task's evidence.
        lead = self.storage.get_lead(str(feedback["leadId"]), **scope) if task_id and feedback.get("leadId") else None
        comment = self.storage.get_comment(str(feedback["commentId"]), **scope) if task_id and feedback.get("commentId") else None

        task = self.storage.get_task(int(task_id), sales_user_id=feedback.get("salesUserId")) if task_id else None
        context = self._task_context(task or {})
        context["learningContext"]["candidateBases"] = self._candidate_bases()
        source_ids = {str(lead.get(k)) for k in ("lead_id", "comment_id") if lead and lead.get(k)}
        source_ids.update(str(feedback[k]) for k in ("leadId", "commentId") if feedback.get(k))
        context["causalFindings"] = [f for f in context["causalFindings"]
                                     if not f.get("targetId") or f["targetId"] in source_ids]
        total = len(context["leadEvidence"])
        context["leadEvidence"] = [row for row in context["leadEvidence"]
                                   if any(str(row.get(k) or "") in source_ids for k in ("lead_id", "comment_id"))]
        note_ids = {str(row.get("note_id")) for row in context["leadEvidence"] if row.get("note_id")}
        note_ids.update(str(row[k]) for row in (lead or {}, comment or {})
                        for k in ("note_id", "noteId") if row.get(k))
        context["noteEvidence"] = [row for row in context["noteEvidence"] if str(row.get("id")) in note_ids]
        context["humanFeedback"] = [row for row in context["humanFeedback"]
                                    if any(str(row.get(k) or "") in source_ids for k in ("leadId", "commentId"))]
        context["humanReviews"] = [row for row in context["humanReviews"] if str(row.get("leadId")) in source_ids]
        qa = context["qa"]
        related_ids = source_ids | note_ids
        context["qa"] = {**_select(qa, ("approved", "batchCount", "reviewedLeadsCount", "expectedLeadsCount")),
                         "issues": [issue for issue in qa.get("issues", []) if issue.get("target_id") in related_ids],
                         "review_requests": [text for text in qa.get("review_requests", [])
                                             if any(re.search(r"(?<![\w:-])" + re.escape(key) + r"(?![\w:-])", str(text))
                                                    for key in related_ids if key)]}
        context["report"] = _select(context["report"], ("summary", "decision"))
        context["evidenceCoverage"] = {"total": total, "included": len(context["leadEvidence"]),
                                       "scope": "feedback_target_and_parent", "completeTaskAudit": False}
        # Stored comments include platform raw responses and identity aliases.
        # A learning request needs the evidence and judgment, not that full record.
        return redact_external({
            **context,
            "feedback": _select(feedback, ("id", "taskId", "leadId", "commentId", "feedbackType",
                                           "feedback", "userNote", "evidence", "expectedResult")),
            "feedbackTypeLabel": FEEDBACK_TYPE_LABELS.get(feedback.get("feedbackType"), feedback.get("feedbackType")),
            "lead": _select(lead, LEAD_FIELDS),
            "comment": _select(comment, ("platform", "commentId", "noteId", "content")),
            "activePrompts": [
                _select(self.registry.effective_prompt(agent_key, context["confirmedPlan"]), ("agentKey", "version", "prompt"))
                for agent_key in sorted(LEARNING_AGENTS)
            ],
            "targetAgentPolicy": {
                "missed_lead": "lead_signal_agent",
                "invalid_lead": "lead_signal_agent",
                "valid_lead": "lead_signal_agent",
                "wrong_product": "critic_qa_agent",
                "bad_outreach": "critic_qa_agent",
                "other": "lead_signal_agent",
            },
        })

    def _candidate_bases(self) -> dict[str, Any]:
        return {key: {"version": self.registry.active_prompt(key).get("version"),
                      "hash": digest(self.registry.active_prompt(key)["prompt"])} for key in LEARNING_AGENTS}

    def _normalize_analysis(self, feedback: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        feedback_type = str(feedback.get("feedbackType") or "")
        target_agents = analysis.get("target_agents") or analysis.get("targetAgents") or []
        if not target_agents:
            target_agents = [_default_target_agent(feedback_type)]
        supported = [agent for agent in target_agents if agent in LEARNING_AGENTS]
        analysis["target_agents"] = supported or [_default_target_agent(feedback_type)]
        if "diagnosis" not in analysis:
            analysis["diagnosis"] = str(analysis.get("decision") or "已根据人工反馈生成改进诊断。")
        if "recommended_action" not in analysis:
            analysis["recommended_action"] = "请管理员审核 Prompt/Skill 改进，通过固定样本验收后再手动启用。"
        return analysis

    def _first_supported_improvement(self, improvements: list[dict[str, Any]]) -> dict[str, Any]:
        for item in improvements:
            if isinstance(item, dict) and str(item.get("agent_key") or "") in LEARNING_AGENTS:
                return item
        raise ValueError("没有支持固定集验收的 Agent 改进。")

    def _task_context(self, task: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
        result = result if result is not None else (task.get("result") or {})
        plan = copy.deepcopy(result.get("confirmedPlan") or (task.get("payload") or {}).get("confirmedPlan") or {})
        plan["offeringId"] = normalize_offering(plan.get("offeringId", GENERAL))
        plan.setdefault("searchGoal", task.get("demandDescription") or "")
        task_id = int(task.get("id") or 0)
        runs = self.storage.list_agent_runs(task_id, limit=2000, run_type="main") if task_id else []
        runs = [run for run in runs if run.get("agentKey") in LEARNING_AGENTS]
        historical_versions = [
            _select(run, ("id", "agentKey", "promptVersion", "model", "runId")) for run in runs
        ]
        context = {"taskId": task_id, "confirmedPlan": plan, "offeringId": plan["offeringId"],
                   "searchGoal": plan["searchGoal"], "keywords": plan.get("keywords", []),
                   "servicePolicyVersion": result.get("servicePolicyVersion") or plan.get("servicePolicyVersion"),
                   "promptVersions": historical_versions,
                   "confirmedPromptRegistry": plan.get("promptRegistry") or []}
        # Unknown historical policy is explicitly unknown, not today's version.
        context["servicePolicyVersion"] = context["servicePolicyVersion"] or next(
            (str(r.get("promptVersion", "")).split(" / policy:", 1)[1]
             for r in runs if " / policy:" in str(r.get("promptVersion", ""))), None)
        leads = ((result.get("leads") or {}).get("leads") or [])
        raw_by_id = {}
        for run in runs:
            if run.get("agentKey") != "lead_signal_agent" or run.get("error"):
                continue
            output = run.get("outputSummary") or {}
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except (ValueError, TypeError):
                    continue
            for row in output.get("leads", []) if isinstance(output, dict) else []:
                if isinstance(row, dict):
                    raw_by_id[str(row.get("candidate_id") or row.get("lead_id") or row.get("comment_id"))] = row
        findings = []
        evidence_rows = []
        note_rows = [_select(note, ("id", "platform", "title", "desc", "relevance", "detailFetched",
                                   "contentQuality", "source_keyword")) for note in result.get("notes", [])]
        qa = result.get("qa") or {}
        review_text = "\n".join(str(x) for x in qa.get("review_requests", []))
        for lead in leads:
            source_id = str((lead.get("source_comment") or {}).get("candidate_id") or lead.get("lead_id") or "")
            raw = lead.get("original_model_decision") or raw_by_id.get(source_id) or raw_by_id.get(str(lead.get("comment_id"))) or {}
            raw = raw if isinstance(raw, dict) else {}
            row = _select(lead, LEAD_FIELDS)
            if raw:
                row["rawModel"] = _select(raw, ("classification", "decision", "intent_role", "service_fit", "demand_origin", "reason"))
            if raw and raw.get("classification") != lead.get("classification"):
                findings.append({"layer": "rule", "targetId": str(lead.get("lead_id") or source_id),
                                 "rawClassification": raw.get("classification"), "finalClassification": lead.get("classification"),
                                 "message": "模型输出被后处理改写；需检查规则，不能仅归因于 Prompt。"})
            target = str(lead.get("lead_id") or "")
            if (target and target in review_text and lead.get("qa_status") == "approved"
                    and lead.get("business_review_required") is not True):
                findings.append({"layer": "qa", "targetId": target, "message": "QA 提出人工复核，但候选状态为 approved。"})
            evidence_rows.append(row)
        feedback_rows = [_select(item, ("id", "taskId", "leadId", "commentId", "feedbackType", "feedback",
                                       "userNote", "evidence", "expectedResult", "status"))
                         for item in self.storage.list_lead_feedback(limit=1000)
                         if item.get("taskId") == task_id and item.get("salesUserId") == task.get("salesUserId")]
        # Analysis/application progress does not change the underlying human evidence.
        for item in feedback_rows:
            item["status"] = "dismissed" if item.get("status") == "dismissed" else "recorded"
        by_target: dict[str, set[str]] = {}
        for item in feedback_rows:
            target = str(item.get("leadId") or item.get("commentId") or "")
            if target and item.get("status") != "dismissed":
                by_target.setdefault(target, set()).add(str(item.get("feedbackType") or ""))
        for target, kinds in by_target.items():
            if "valid_lead" in kinds and kinds.intersection({"invalid_lead", "irrelevant", "wrong_evidence"}):
                findings.append({"layer": "feedback", "targetId": target,
                                 "message": "同一候选存在有效与无效/证据不支持的冲突反馈，须人工裁定后再学习。"})
        human_reviews = []
        if task_id:
            with self.storage.connect() as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='human_reviews'").fetchone()
                records = db.execute("SELECT id,lead_id,basis,status,note,created FROM human_reviews WHERE task_id=? ORDER BY id",
                                     (task_id,)).fetchall() if exists else []
            current_bases = {str(lead.get("lead_id")): WorkbenchStore.review_basis(lead, plan) for lead in leads}
            for row in records:
                human_reviews.append({"id": row["id"], "leadId": row["lead_id"], "status": row["status"],
                    "note": row["note"], "createdAt": row["created"], "reviewer": "管理员",
                    "appliesToCurrentEvidence": current_bases.get(row["lead_id"]) == row["basis"]})
            latest_reviews = {row["leadId"]: row for row in human_reviews if row["appliesToCurrentEvidence"]}
            for target, review in latest_reviews.items():
                kinds = by_target.get(target, set())
                if ((review["status"] == "approved" and kinds.intersection({"invalid_lead", "irrelevant", "wrong_evidence"}))
                        or (review["status"] == "rejected" and "valid_lead" in kinds)):
                    findings.append({"layer": "feedback", "targetId": target,
                        "message": "正式人工审核与反馈期望矛盾，须先统一人工结论，不能自动生成 Prompt 候选。"})
        scope = redact_external(context)
        return redact_external({**scope, "plan": plan, "learningContext": scope,
                                "learningPolicy": LEARNING_POLICY, "causalFindings": findings,
                                "leadEvidence": evidence_rows, "noteEvidence": note_rows,
                                "humanFeedback": feedback_rows, "humanReviews": human_reviews,
                                "qa": qa, "report": result.get("report") or {},
                                "collectionSummary": result.get("collectionSummary") or {},
                                "stageCoverage": result.get("stageCoverage") or {}})

    def reflect_task(self, task_id: int, *, result: dict[str, Any] | None = None,
                     runner: Any = None, auto_save_candidates: bool = True, retry: bool = False) -> dict[str, Any]:
        """Task-end hook. No platform calls, activation, or synthetic acceptance data."""
        with _LEARNING_LOCK:
            task = self.storage.get_task(task_id)
            if not task:
                raise ValueError("任务不存在。")
            context = self._task_context(task, result)
            # Exclude evolving summaries and this service's own agent runs.
            evidence_hash = digest(context)
            saved = self.storage.get_task_summary(task_id) or {}
            previous = saved.get("evolution") or {}
            if previous.get("evidenceHash") == evidence_hash and (previous.get("status") != "failed" or not retry):
                return previous
            evolution = {"status": "pending", "summary": "", "candidatePromptIds": [], "error": "",
                         "requiresEvaluation": True, "evidenceHash": evidence_hash,
                         "learningContext": context["learningContext"]}
            try:
                context["learningContext"]["candidateBases"] = self._candidate_bases()
                if runner is None:
                    llm = self.config.get("llm") or {}
                    if not has_llm_key(llm.get("api_key"), llm.get("api_base")):
                        raise ValueError("复盘未运行：缺少模型配置。")
                    self.registry.ensure_defaults()
                    runner = StructuredAgentRunner(self.config, self.storage, task_id, self.registry,
                                                   run_id=f"reflection-{evidence_hash[:16]}", run_type="evolution")
                # Bound the model request, explicitly record any omitted evidence.
                evidence = context["leadEvidence"]
                targets = {f["targetId"] for f in context["causalFindings"] if f.get("targetId")}
                blocked_ids = targets | {str(row.get(k)) for row in evidence
                               if str(row.get("lead_id")) in targets or str(row.get("comment_id")) in targets
                               for k in ("lead_id", "comment_id", "note_id") if row.get(k)}
                selected = sorted(evidence, key=lambda x: (str(x.get("lead_id")) not in targets,
                                                          x.get("qa_status") not in {"rejected", "needs_review"}))[:40]
                request = {**context, "leadEvidence": selected,
                           "evidenceCoverage": {"total": len(evidence), "included": len(selected),
                                                "complete": len(evidence) == len(selected)}}
                analysis = runner.run("reflection_evolution_agent", request, purpose="任务复盘：仅生成待验收建议")
                # Also protect callers that supply a custom runner. The normal
                # runner validates this contract before recording model success.
                analysis = normalize_reflection_output(analysis)
                blocked = []
                for index, item in enumerate(analysis.get("prompt_improvements", [])):
                    if not isinstance(item, dict):
                        blocked.append("改进项格式无效。")
                        continue
                    evidence_ids = item.get("evidence_ids") or []
                    supported_ids = {str(row.get(k)) for row in selected for k in ("lead_id", "comment_id", "note_id") if row.get(k)}
                    supported_ids.update(str(note["id"]) for note in context["noteEvidence"] if note.get("id"))
                    if (item.get("cause_layer") != "prompt" or item.get("agent_key") not in LEARNING_AGENTS
                            or not isinstance(evidence_ids, list) or not evidence_ids
                            or not all(isinstance(v, str) and v in supported_ids for v in evidence_ids)
                            or any(v in blocked_ids for v in evidence_ids)):
                        blocked.append("改进缺少可核对的 Prompt 根因，或涉及规则/QA冲突；保留人工建议。")
                        continue
                    if auto_save_candidates:
                        candidate_id = self.registry.create_learning_candidate(
                            item["agent_key"], str(item.get("prompt_patch") or ""),
                            context=context["learningContext"], source_key=f"task:{task_id}:{evidence_hash}:{index}",
                            source_task_id=task_id, title=str(item.get("title") or "任务复盘候选"))
                        evolution["candidatePromptIds"].append(candidate_id)
                for case in analysis.get("eval_cases") or []:
                    if isinstance(case, dict):
                        self.storage.save_eval_case(task_id, {**case, "source": "reflection_suggestion",
                            "humanVerified": False, "eligibleForAcceptance": False,
                            "learningContext": context["learningContext"]})
                evolution.update(status="awaiting_evaluation" if evolution["candidatePromptIds"] else "needs_review",
                                 summary=analysis["evolution_summary"],
                                 evaluationStatus="awaiting_frozen_dataset", blockedSuggestions=blocked,
                                 evidenceCoverage=request["evidenceCoverage"])
                saved["reflectionAnalysis"] = redact_external(analysis)
                saved["causalFindings"] = context["causalFindings"]
            except Exception as exc:
                evolution.update(status="failed", summary="复盘未完成，未启用任何候选；可显式重试。",
                                 error=str(redact_external(str(exc))))
            saved["evolution"] = evolution
            saved["evolution_summary"] = evolution["summary"]
            self.storage.save_task_summary(task_id, saved)
            return evolution


def _default_target_agent(feedback_type: str) -> str:
    if feedback_type in {"wrong_product", "bad_outreach"}:
        return "critic_qa_agent"
    if feedback_type == "other":
        return "lead_signal_agent"
    return "lead_signal_agent"
