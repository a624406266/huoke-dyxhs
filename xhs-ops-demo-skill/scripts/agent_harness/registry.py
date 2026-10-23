from __future__ import annotations

from typing import Any
import threading

from .agents import DEFAULT_AGENT_SPECS, AgentSpec, agent_specs

PROMPT_ITERATION_START = "V0.1"
LEARNING_AGENTS = frozenset({"relevance_agent", "lead_signal_agent", "critic_qa_agent", "report_agent"})
_REGISTRY_LOCK = threading.RLock()


class PromptRegistry:
    def __init__(self, storage: Any | None = None) -> None:
        self.storage = storage

    def ensure_defaults(self) -> None:
        if not self.storage:
            return
        for spec in agent_specs():
            self.storage.ensure_prompt_version(
                agent_key=spec.key,
                agent_name=spec.name,
                version=spec.version,
                prompt=spec.system_prompt,
                status="active",
                score=0.0,
                notes="Built-in Harness prompt.",
                iteration_version=PROMPT_ITERATION_START,
            )

    def active_prompt(self, agent_key: str) -> dict[str, Any]:
        spec = self.spec(agent_key)
        if self.storage:
            row = self.storage.get_active_prompt(agent_key)
            if row:
                return row
        return {
            "agentKey": spec.key,
            "agentName": spec.name,
            "version": spec.version,
            "iterationVersion": PROMPT_ITERATION_START,
            "prompt": spec.system_prompt,
            "status": "active",
            "score": 0.0,
            "notes": "Built-in Harness prompt.",
        }

    def effective_prompt(self, agent_key: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.resolve_for_plan(self.active_prompt(agent_key), plan or {})

    def resolve_for_plan(self, prompt: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """A globally selected scoped candidate must not affect another offering."""
        from .offering import GENERAL, normalize_offering
        offering = normalize_offering(plan.get("offeringId", GENERAL))
        agent_key = str(prompt["agentKey"])
        versions = {p.get("id"): p for p in self.prompt_versions(agent_key) if p.get("id")}
        current, seen = prompt, set()
        while True:
            metadata = current.get("metrics") or {}
            scope = (metadata.get("learningContext") or {}).get("offeringId")
            if current.get("status") != "disabled" and (not scope or scope == offering):
                return current
            parent_id = metadata.get("basePromptId")
            if not parent_id or parent_id in seen or parent_id not in versions:
                break
            seen.add(parent_id)
            current = versions[parent_id]
        spec = self.spec(agent_key)
        if any(row.get("version") == spec.version and row.get("status") == "disabled" for row in versions.values()):
            raise ValueError("该服务模式没有可用的基础版本；不能重新使用已禁用 Prompt，请管理员恢复有效版本。")
        return {"agentKey": agent_key, "agentName": spec.name, "version": spec.version,
                "iterationVersion": PROMPT_ITERATION_START, "prompt": spec.system_prompt,
                "status": "active", "score": 0.0, "notes": "Built-in Harness prompt.",
                "scopeFallback": True}

    def spec(self, agent_key: str) -> AgentSpec:
        if agent_key not in DEFAULT_AGENT_SPECS:
            raise KeyError(f"未知 Agent：{agent_key}")
        return DEFAULT_AGENT_SPECS[agent_key]

    def agents_payload(self) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for spec in agent_specs():
            active = self.active_prompt(spec.key)
            payload.append(
                {
                    "key": spec.key,
                    "name": spec.name,
                    "version": active.get("iterationVersion") or PROMPT_ITERATION_START,
                    "iterationVersion": active.get("iterationVersion") or PROMPT_ITERATION_START,
                    "promptVersion": active.get("version", spec.version),
                    "role": spec.role,
                    "requiredKeys": list(spec.required_keys),
                }
            )
        return payload

    def prompt_versions(self, agent_key: str | None = None) -> list[dict[str, Any]]:
        if not self.storage:
            prompts = [self.active_prompt(spec.key) for spec in agent_specs()]
            return [prompt for prompt in prompts if not agent_key or prompt.get("agentKey") == agent_key]
        return self.storage.list_prompt_versions(agent_key=agent_key)

    def promote(self, prompt_id: int) -> dict[str, Any]:
        if not self.storage:
            raise RuntimeError("当前存储不可用，无法切换 Prompt。")
        with _REGISTRY_LOCK:
            return self.storage.promote_prompt_version(prompt_id)

    def create_learning_candidate(self, agent_key: str, patch: str, *, context: dict[str, Any],
                                  source_key: str, source_task_id: int, title: str = "复盘改进候选") -> int:
        with _REGISTRY_LOCK:
            return self._create_learning_candidate(agent_key, patch, context=context, source_key=source_key,
                                                   source_task_id=source_task_id, title=title)

    def _create_learning_candidate(self, agent_key: str, patch: str, *, context: dict[str, Any],
                                   source_key: str, source_task_id: int, title: str) -> int:
        """Save a scoped, traceable candidate; never make it active."""
        from workbench_store import digest
        if not self.storage or agent_key not in LEARNING_AGENTS or not patch.strip():
            raise ValueError("只支持可评测 Agent 的非空 Prompt 改进。")
        self.ensure_defaults()
        active = self.active_prompt(agent_key)
        effective_base = self.resolve_for_plan(active, context)
        expected_base = (context.get("candidateBases") or {}).get(agent_key)
        if expected_base and expected_base != {"version": active.get("version"), "hash": digest(active["prompt"])}:
            raise ValueError("分析后活跃 Prompt 已变化，请重新分析，不能将旧建议直接追加到新版本。")
        candidate_key = digest({"source": source_key, "agent": agent_key, "patch": patch})
        for row in self.prompt_versions(agent_key):
            if (row.get("metrics") or {}).get("learningCandidateKey") == candidate_key:
                return int(row["id"])
        scope = str(context.get("offeringId") or "general")
        prompt = (str(effective_base["prompt"]) + "\n\n# 待验收的场景改进\n"
                  + f"以下补充仅适用于 confirmedPlan/plan.offeringId={scope} 的任务；其他模式保持原规则。\n"
                  + "不能覆盖服务资格、证据归属、人工确认或数据安全规则。\n" + patch.strip())
        candidate_id = self.storage.create_prompt_candidate(
            agent_key=agent_key, agent_name=active.get("agentName", agent_key),
            base_version=active.get("version", ""), prompt=prompt,
            source_task_id=source_task_id, notes=title)
        self.storage.update_prompt_score(candidate_id, 0.0, {
            "learningCandidateKey": candidate_key, "learningContext": context,
            "basePromptId": active.get("id"), "basePromptVersion": active.get("version"),
            "basePromptHash": digest(active["prompt"]),
            "composedBasePromptId": effective_base.get("id"), "composedBasePromptHash": digest(effective_base["prompt"]),
            "evaluationStatus": "awaiting_frozen_dataset",
            "sourceKey": source_key, "promoted": False,
        })
        return candidate_id

    def promote_evaluated(self, prompt_id: int, eval_run_id: str) -> dict[str, Any]:
        with _REGISTRY_LOCK:
            return self._promote_evaluated(prompt_id, eval_run_id)

    def _promote_evaluated(self, prompt_id: int, eval_run_id: str) -> dict[str, Any]:
        """Administrator entry point: matching frozen proof and unchanged base."""
        from workbench_store import WorkbenchStore, digest
        from .offering import SERVICE_POLICY_VERSION, focused
        candidate = next((p for p in self.prompt_versions() if p.get("id") == prompt_id), None)
        if not candidate or candidate.get("status") == "disabled":
            raise ValueError("候选不存在或已禁用。")
        proof = WorkbenchStore(self.storage).promotion_proof(prompt_id, candidate["prompt"], eval_run_id)
        metadata = candidate.get("metrics") or {}
        active = self.active_prompt(candidate["agentKey"])
        if active.get("id") != prompt_id and metadata.get("basePromptHash"):
            if (metadata["basePromptHash"] != digest(active["prompt"])
                    or metadata.get("basePromptVersion") != active.get("version")):
                raise ValueError("当前活跃版本已变化；请基于当前版本重新生成候选并评测。")
        context = metadata.get("learningContext") or {}
        scope = context.get("offeringId")
        if scope and (not proof.get("offeringCounts", {}).get(scope)
                      or set(proof.get("offeringCounts", {})) != {scope}):
            raise ValueError("验收集的服务模式与候选来源不一致。")
        if focused(context) and proof.get("servicePolicyVersion") != SERVICE_POLICY_VERSION:
            raise ValueError("服务规则版本已变化，需重新执行验收集。")
        if candidate["agentKey"] in {"critic_qa_agent", "report_agent"}:
            if proof.get("agentKey") != candidate["agentKey"] or not proof.get("qualityReviewComplete"):
                raise ValueError("该 Agent 需专用冻结集及逐条人工质量验收。")
        result = self.promote(prompt_id)
        self.storage.update_prompt_score(prompt_id, candidate.get("score", 0),
                                         {**metadata, "acceptedEvalRunId": eval_run_id, "promoted": True})
        result["metrics"] = {**metadata, "acceptedEvalRunId": eval_run_id, "promoted": True}
        return result

    def rollback(self, agent_key: str, prompt_id: int) -> dict[str, Any]:
        with _REGISTRY_LOCK:
            return self._rollback(agent_key, prompt_id)

    def _rollback(self, agent_key: str, prompt_id: int) -> dict[str, Any]:
        """Return only to an actually activated historical version, never a proposal."""
        if not self.storage:
            raise RuntimeError("当前存储不可用，无法回退。")
        self.spec(agent_key)
        target = next((p for p in self.prompt_versions(agent_key) if p.get("id") == prompt_id), None)
        if not target or target.get("activatedAt") is None:
            raise ValueError("只能回退到该 Agent 曾实际启用的历史版本。")
        return self.storage.promote_prompt_version(prompt_id)

    def disable(self, prompt_id: int) -> dict[str, Any]:
        if not self.storage:
            raise RuntimeError("当前存储不可用，无法禁用 Prompt。")
        with _REGISTRY_LOCK:
            return self.storage.disable_prompt_version(prompt_id)

    def summary(self) -> list[dict[str, Any]]:
        if not self.storage:
            return []
        summary = self.storage.prompt_version_summary()
        for row in summary:
            if row.get("agentKey") in DEFAULT_AGENT_SPECS:
                row["active"] = self.active_prompt(row["agentKey"])
        return summary
