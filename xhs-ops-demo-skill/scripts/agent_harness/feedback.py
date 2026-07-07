from __future__ import annotations

from typing import Any

from llm_client import has_llm_key

from .registry import PromptRegistry
from .runtime import StructuredAgentRunner


FEEDBACK_TYPE_LABELS = {
    "valid_lead": "有效线索",
    "invalid_lead": "误判线索",
    "missed_lead": "漏识别线索",
    "wrong_product": "产品匹配不准",
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
        if not has_llm_key(llm_config.get("api_key")):
            raise RuntimeError("反馈学习必须配置 LLM API Key；请先在设置中填写 API Key 或设置 LLM_API_KEY/OPENAI_API_KEY。")
        feedback = self._feedback(feedback_id)
        self.registry.ensure_defaults()
        runner = StructuredAgentRunner(
            self.config,
            self.storage,
            int(feedback.get("taskId") or 0),
            self.registry,
        )
        analysis = runner.run(
            "feedback_learning_agent",
            self._analysis_context(feedback),
            purpose="人工反馈学习分析",
        )
        analysis = self._normalize_analysis(feedback, analysis)
        target_agent_key = (analysis.get("target_agents") or [""])[0]
        self.storage.save_lead_feedback_analysis(feedback_id, analysis, target_agent_key=target_agent_key)
        return self._feedback(feedback_id)

    def apply(self, feedback_id: int) -> dict[str, Any]:
        feedback = self._feedback(feedback_id)
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
        self.registry.ensure_defaults()
        active = self.storage.get_active_prompt(agent_key)
        if not active:
            raise ValueError(f"找不到 {agent_key} 的当前 Prompt 版本。")
        candidate_prompt = str(active.get("prompt") or "") + "\n\n# 人工反馈学习候选\n" + prompt_patch
        candidate_id = self.storage.create_prompt_candidate(
            agent_key=agent_key,
            agent_name=str(active.get("agentName") or agent_key),
            base_version=str(active.get("version") or "v1"),
            prompt=candidate_prompt,
            source_task_id=int(feedback.get("taskId") or 0),
            notes=str(item.get("title") or "人工反馈学习改进"),
        )
        self.storage.promote_prompt_version(candidate_id)
        eval_case = analysis.get("eval_case") or analysis.get("evalCase") or {}
        if eval_case:
            self.storage.save_eval_case(int(feedback.get("taskId") or 0), eval_case)
        self.storage.mark_lead_feedback_applied(feedback_id, candidate_id, agent_key)
        return {
            "feedback": self._feedback(feedback_id),
            "activePrompt": self.storage.get_active_prompt(agent_key),
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
        lead = self.storage.get_lead(str(feedback.get("leadId") or "")) if feedback.get("leadId") else None
        comment = self.storage.get_comment(str(feedback.get("commentId") or "")) if feedback.get("commentId") else None
        return {
            "feedback": feedback,
            "feedbackTypeLabel": FEEDBACK_TYPE_LABELS.get(feedback.get("feedbackType"), feedback.get("feedbackType")),
            "lead": lead or {},
            "comment": comment or {},
            "activePrompts": [
                self.registry.active_prompt(agent_key)
                for agent_key in ["relevance_agent", "lead_signal_agent", "product_strategy_agent"]
            ],
            "targetAgentPolicy": {
                "missed_lead": "lead_signal_agent",
                "invalid_lead": "lead_signal_agent",
                "valid_lead": "lead_signal_agent",
                "wrong_product": "product_strategy_agent",
                "bad_outreach": "product_strategy_agent",
                "other": "lead_signal_agent",
            },
        }

    def _normalize_analysis(self, feedback: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        feedback_type = str(feedback.get("feedbackType") or "")
        target_agents = analysis.get("target_agents") or analysis.get("targetAgents") or []
        if not target_agents:
            target_agents = [_default_target_agent(feedback_type)]
        supported = [agent for agent in target_agents if agent in {"relevance_agent", "lead_signal_agent", "product_strategy_agent"}]
        analysis["target_agents"] = supported or [_default_target_agent(feedback_type)]
        if "diagnosis" not in analysis:
            analysis["diagnosis"] = str(analysis.get("decision") or "已根据人工反馈生成改进诊断。")
        if "recommended_action" not in analysis:
            analysis["recommended_action"] = "请管理员审核 Prompt/Skill 改进，确认后应用为当前版本。"
        return analysis

    def _first_supported_improvement(self, improvements: list[dict[str, Any]]) -> dict[str, Any]:
        for item in improvements:
            if str(item.get("agent_key") or "") in {"relevance_agent", "lead_signal_agent", "product_strategy_agent"}:
                return item
        return improvements[0]


def _default_target_agent(feedback_type: str) -> str:
    if feedback_type in {"wrong_product", "bad_outreach"}:
        return "product_strategy_agent"
    if feedback_type == "other":
        return "lead_signal_agent"
    return "lead_signal_agent"
