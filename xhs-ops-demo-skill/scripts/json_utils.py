"""Shared JSON encoding and explicit fallback parsing, without runtime state."""
from __future__ import annotations

import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse_json(value: Any, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value, parse_constant=_invalid_constant)
    except (TypeError, ValueError, UnicodeDecodeError):
        return default


def _invalid_constant(value: str) -> None:
    raise ValueError("JSON 不接受非有限数值。")
