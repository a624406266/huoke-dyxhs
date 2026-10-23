"""Pure planning: this module never constructs or invokes a platform adapter."""
from __future__ import annotations

import math
import re
from typing import Any

from .runtime import StructuredAgentRunner
from .offering import GENERAL, SERVICE_GOAL, fallback_keywords, focused, normalize_offering


def _words(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[,，;；\n]+", value)
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError("关键词必须是字符串数组或逗号/换行分隔的文本。")
    return list(dict.fromkeys(x.strip() for x in value if x.strip()))


def normalize_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("搜索计划必须是对象。")
    seeds = _words(payload.get("seedKeywords", payload.get("keyword", payload.get("keywords"))))
    keywords = _words(payload.get("keywords", seeds))
    if not seeds or not keywords:
        raise ValueError("至少提供一个种子词和一个实际搜索词。")
    reasons = payload.get("keywordReasons") or {}
    if not isinstance(reasons, dict):
        raise ValueError("keywordReasons 必须是对象。")
    result: dict[str, Any] = {
        "offeringId": normalize_offering(payload.get("offeringId", GENERAL)),
        "seedKeywords": seeds,
        "searchGoal": str(payload.get("searchGoal", payload.get("demandDescription", payload.get("demand_description", ""))) or "").strip(),
        "excludeKeywords": _words(payload.get("excludeKeywords")),
        "keywords": keywords,
        "keywordReasons": {str(k): str(v) for k, v in reasons.items() if k in keywords},
    }
    if focused(result) and not result["searchGoal"]:
        result["searchGoal"] = SERVICE_GOAL
    defaults = {"maxKeywords": 6, "maxPagesPerKeyword": 1, "maxNotes": 20,
                "maxCommentsPerNote": 20, "maxCommentsTotal": 200}
    for key, default in defaults.items():
        value = payload.get(key, payload.get("count", default) if key == "maxNotes" else default)
        if isinstance(value, bool):
            raise ValueError(f"{key} 必须是整数。")
        try:
            number = int(value)
            if float(value) != number or number < 1:
                raise ValueError()
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{key} 必须是正整数。") from exc
        result[key] = number
    if len(keywords) > result["maxKeywords"]:
        raise ValueError("实际搜索词数量超过 maxKeywords，请调整计划后重新确认。")
    for key, default in (("delayMin", 1.0), ("delayMax", 3.0)):
        try:
            result[key] = float(payload.get(key, default))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{key} 必须是非负秒数。") from exc
        if not math.isfinite(result[key]) or result[key] < 0:
            raise ValueError(f"{key} 必须是有限非负秒数。")
    if result["delayMax"] < result["delayMin"]:
        raise ValueError("delayMax 不得小于 delayMin。")
    platforms = _words(payload.get("platforms", [payload.get("platform", "xhs")]))
    if not platforms or any(x not in {"xhs", "douyin"} for x in platforms):
        raise ValueError("platforms 只支持 xhs、douyin。")
    result["platforms"] = platforms
    if "douyin" in platforms and result["maxPagesPerKeyword"] > 1:
        raise ValueError("抖音读取接口尚未支持分页；包含抖音的计划请将每个关键词页数设为 1。")
    for key, default, options in (
        ("timeFilter", "all", {"all", "day", "week", "month", "quarter", "year"}),
        ("sort", "popular", {"popular", "general", "latest"}),
        ("metric", "engagement", {"engagement", "liked", "collected", "comments", "shared"}),
    ):
        result[key] = str(payload.get(key, default))
        if result[key] not in options:
            raise ValueError(f"不支持的 {key}：{result[key]}")
    for key in ("readDetail", "scanLeads"):
        value = payload.get(key, True)
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值。")
        result[key] = value
    try:
        raw_score = payload.get("minScore", 60)
        score = int(raw_score)
        if isinstance(raw_score, bool) or float(raw_score) != score:
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("minScore 必须是 0 到 100 的整数。") from exc
    if not 0 <= score <= 100:
        raise ValueError("minScore 必须是 0 到 100 的整数。")
    result["minScore"] = score
    return result


def plan_search(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    plan = normalize_plan(payload)
    try:
        output = StructuredAgentRunner(config).run("planning_agent", plan, purpose="仅生成可编辑搜索计划；不执行采集")
        keywords = _words(output.get("keywords"))
        if not keywords:
            raise ValueError("规划没有返回搜索词。")
        draft = normalize_plan({**plan, "keywords": keywords,
                                "keywordReasons": output.get("keywordReasons") or {}})
        return {**draft, "status": "draft", "planningMode": "llm", "requiresConfirmation": True}
    except Exception:
        # No model failure is silently represented as an AI-generated plan.
        if focused(plan):
            keywords = fallback_keywords(plan["seedKeywords"], plan["maxKeywords"])
            return {**plan, "keywords": keywords,
                    "keywordReasons": {word: "规则草案：种子词结合委托开发意图，需人工确认。" for word in keywords},
                    "status": "draft", "planningMode": "manual", "requiresConfirmation": True,
                    "error": "AI 规划不可用，当前为定制开发需求的规则草案，请手动编辑并确认；尚未搜索。"}
        return {**plan, "status": "draft", "planningMode": "manual", "requiresConfirmation": True,
                "error": "AI 规划不可用，当前为原始关键词草案，请检查模型设置或手动编辑后确认。"}
