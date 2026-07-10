from __future__ import annotations

from typing import Any

from .agents import DEFAULT_AGENT_SPECS, AgentSpec, agent_specs

PROMPT_ITERATION_START = "V0.1"


class PromptRegistry:
    def __init__(self, storage: Any | None = None) -> None:
        self.storage = storage

    def ensure_defaults(self) -> None:
        if not self.storage:
            return
        for spec in agent_specs():
            active = self.storage.get_active_prompt(spec.key)
            prompt_id = self.storage.ensure_prompt_version(
                agent_key=spec.key,
                agent_name=spec.name,
                version=spec.version,
                prompt=spec.system_prompt,
                status="active",
                score=0.82,
                notes="Built-in Harness prompt.",
                iteration_version=PROMPT_ITERATION_START,
            )
            if active and active.get("version") != spec.version and active.get("notes") == "Built-in Harness prompt.":
                self.storage.promote_prompt_version(prompt_id)

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
            "score": 0.82,
        }

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
        return self.storage.promote_prompt_version(prompt_id)

    def disable(self, prompt_id: int) -> dict[str, Any]:
        if not self.storage:
            raise RuntimeError("当前存储不可用，无法禁用 Prompt。")
        return self.storage.disable_prompt_version(prompt_id)

    def summary(self) -> list[dict[str, Any]]:
        if not self.storage:
            return []
        return self.storage.prompt_version_summary()
