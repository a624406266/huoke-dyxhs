from __future__ import annotations

from typing import Any

from .agents import DEFAULT_AGENT_SPECS, AgentSpec, agent_specs


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
                score=0.82,
                notes="Built-in Harness prompt.",
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
            "prompt": spec.system_prompt,
            "status": "active",
            "score": 0.82,
        }

    def spec(self, agent_key: str) -> AgentSpec:
        if agent_key not in DEFAULT_AGENT_SPECS:
            raise KeyError(f"未知 Agent：{agent_key}")
        return DEFAULT_AGENT_SPECS[agent_key]

    def agents_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "key": spec.key,
                "name": spec.name,
                "version": self.active_prompt(spec.key).get("version", spec.version),
                "role": spec.role,
                "requiredKeys": list(spec.required_keys),
            }
            for spec in agent_specs()
        ]

    def prompt_versions(self) -> list[dict[str, Any]]:
        if not self.storage:
            return [self.active_prompt(spec.key) for spec in agent_specs()]
        return self.storage.list_prompt_versions()

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
