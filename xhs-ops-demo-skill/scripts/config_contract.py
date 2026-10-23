"""Side-effect-free validation shared by settings and model transport."""
from __future__ import annotations

import math
from typing import Any


LLM_TIMEOUT_DEFAULTS = {
    "connect_timeout_s": 30,
    "read_timeout_s": 60,
    "total_timeout_s": 300,
}


def finite_number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    message = f"{field} 必须为 {minimum:g}–{maximum:g} 之间的有限数值。"
    if isinstance(value, bool):
        raise ValueError(message)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(message)
    return number


def llm_temperature(value: Any) -> float:
    return finite_number(value, "llm.temperature", minimum=0, maximum=2)


def llm_timeout_seconds(value: Any, field: str) -> int:
    message = f"llm.{field} 必须为 1–86400 的整数秒。"
    try:
        seconds = finite_number(value, f"llm.{field}", minimum=1, maximum=86400)
    except ValueError as exc:
        raise ValueError(message) from exc
    if not seconds.is_integer():
        raise ValueError(message)
    return int(seconds)


def boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} 必须为布尔值。")
    return value


def platform_settings(current: dict[str, Any], payload: dict[str, Any], platform: str) -> dict[str, Any]:
    """Validate shared platform settings before the caller persists any changes."""
    allowed = {"enable_follow_actions", "enable_dm_actions"}
    if platform == "xhs":
        allowed.add("bin")
    result = {key: value for key, value in current.items() if key in allowed}
    for field in ("enable_follow_actions", "enable_dm_actions"):
        if field in payload:
            result[field] = boolean(payload[field], f"{platform}.{field}")
    if platform == "xhs" and "bin" in payload:
        result["bin"] = str(payload["bin"] or "").strip()
    return result
