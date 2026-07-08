from __future__ import annotations

import json
import time
from typing import Any, Callable

from llm_client import call_llm_json, resolve_llm_config

from .registry import PromptRegistry


class StructuredAgentRunner:
    def __init__(
        self,
        config: dict[str, Any],
        storage: Any | None = None,
        task_id: int | None = None,
        registry: PromptRegistry | None = None,
        llm_json_callable: Callable[..., tuple[dict[str, Any], str]] | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.task_id = task_id
        self.registry = registry or PromptRegistry(storage)
        self.llm_json_callable = llm_json_callable or call_llm_json

    def run(self, agent_key: str, payload: dict[str, Any], *, purpose: str = "") -> dict[str, Any]:
        spec = self.registry.spec(agent_key)
        prompt = self.registry.active_prompt(agent_key)
        llm_config = self.config.get("llm") or {}
        _, _, model, temperature = resolve_llm_config(
            api_key=llm_config.get("api_key"),
            api_base=llm_config.get("api_base"),
            model=llm_config.get("model"),
            temperature=llm_config.get("temperature"),
        )
        start = time.time()
        error = ""
        raw = ""
        output: dict[str, Any] = {}
        input_summary = _compact(payload)
        try:
            messages = [
                {"role": "system", "content": str(prompt.get("prompt") or spec.system_prompt)},
                {
                    "role": "system",
                    "content": (
                        "如果用户输入中包含 outputContract，必须优先满足其中的覆盖率、字段和枚举要求。"
                        "当 outputContract 要求对每个输入实体返回判断时，不得只返回正例或高置信项；"
                        "非商机、低分、低置信和丢弃项也必须逐条返回可审计 reason。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            output, raw = self.llm_json_callable(
                messages,
                api_key=llm_config.get("api_key"),
                api_base=llm_config.get("api_base"),
                model=llm_config.get("model"),
                temperature=llm_config.get("temperature"),
                required_keys=list(spec.required_keys),
                retries=2,
            )
            self._validate_common(agent_key, output)
            return output
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = int((time.time() - start) * 1000)
            if self.storage and self.task_id:
                self.storage.save_agent_run(
                    task_id=self.task_id,
                    agent_key=agent_key,
                    agent_name=spec.name,
                    prompt_version=str(prompt.get("version") or spec.version),
                    model=model,
                    purpose=purpose,
                    input_summary=input_summary,
                    output_summary=_compact(output or raw),
                    confidence=float(output.get("confidence", 0.0)) if isinstance(output, dict) else 0.0,
                    error=error[:1000],
                    duration_ms=duration_ms,
                )

    def _validate_common(self, agent_key: str, output: dict[str, Any]) -> None:
        if not isinstance(output, dict):
            raise ValueError(f"{agent_key} 输出不是 JSON 对象。")
        confidence = output.get("confidence")
        if confidence is None:
            raise ValueError(f"{agent_key} 缺少 confidence。")
        try:
            confidence_value = float(confidence)
        except Exception as exc:
            raise ValueError(f"{agent_key} confidence 不是数字。") from exc
        if confidence_value < 0 or confidence_value > 1:
            raise ValueError(f"{agent_key} confidence 必须在 0 到 1 之间。")
        if not isinstance(output.get("evidence"), list):
            raise ValueError(f"{agent_key} evidence 必须是数组。")
        if not isinstance(output.get("risks"), list):
            raise ValueError(f"{agent_key} risks 必须是数组。")
        if not isinstance(output.get("next_actions"), list):
            raise ValueError(f"{agent_key} next_actions 必须是数组。")


def _compact(value: Any, limit: int = 4000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]
