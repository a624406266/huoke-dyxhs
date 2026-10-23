from __future__ import annotations

import copy
import json
import os
import platform
import re
from pathlib import Path
from typing import Any

from config_contract import LLM_TIMEOUT_DEFAULTS, boolean, llm_temperature, llm_timeout_seconds, platform_settings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "xhs": {
        "bin": "",
        "enable_follow_actions": True,
        "enable_dm_actions": True,
    },
    "platforms": {
        "enabled": ["xhs"],
    },
    "opencli": {
        "bin": "",
    },
    "socialdatax": {
        "api_key_env": "SOCIALDATAX_API_KEY",
    },
    "douyin": {
        "enable_follow_actions": True,
        "enable_dm_actions": True,
    },
    "llm": {
        "api_key": "",
        "api_base": "",
        "model": "",
        "temperature": 0.6,
        **LLM_TIMEOUT_DEFAULTS,
    },
    "sales": {
        "redact_user": False,
    },
    "collection": {"maxKeywords": 6, "maxPagesPerKeyword": 1, "maxNotes": 20, "maxCommentsPerNote": 20, "maxCommentsTotal": 200, "delayMin": 1, "delayMax": 3},
    "storage": {
        "db_path": str(ROOT / "data" / "keyword_workbench_v3.db"),
    },
    "auth": {
        "admin_username": "admin",
        "admin_display_name": "管理员",
        "admin_password_hash": "",
        "session_ttl_seconds": 86400,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("配置文件必须包含 JSON 对象。")
    return value


def local_config_path() -> Path:
    return ROOT / "config.local.json"


def load_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config = deep_merge(config, read_json(ROOT / "config.example.json"))
    config = deep_merge(config, read_json(ROOT / "config.local.json"))

    if os.environ.get("PORT"):
        config["server"]["port"] = int(os.environ["PORT"])
    if os.environ.get("HOST"):
        config["server"]["host"] = os.environ["HOST"]
    if os.environ.get("XHS_BIN"):
        config["xhs"]["bin"] = os.environ["XHS_BIN"]
    if os.environ.get("OPENCLI_BIN"):
        config["opencli"]["bin"] = os.environ["OPENCLI_BIN"]
    if os.environ.get("LLM_API_KEY"):
        config["llm"]["api_key"] = os.environ["LLM_API_KEY"]
    if os.environ.get("OPENAI_API_KEY") and not config["llm"].get("api_key"):
        config["llm"]["api_key"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("LLM_API_BASE"):
        config["llm"]["api_base"] = os.environ["LLM_API_BASE"]
    if os.environ.get("LLM_MODEL"):
        config["llm"]["model"] = os.environ["LLM_MODEL"]
    if os.environ.get("XHS_DB_PATH"):
        config["storage"]["db_path"] = os.environ["XHS_DB_PATH"]
    if os.environ.get("XHS_ADMIN_USERNAME"):
        config["auth"]["admin_username"] = os.environ["XHS_ADMIN_USERNAME"]
    if os.environ.get("XHS_ADMIN_PASSWORD_HASH"):
        config["auth"]["admin_password_hash"] = os.environ["XHS_ADMIN_PASSWORD_HASH"]
    if os.environ.get("SESSION_TTL_SECONDS"):
        config["auth"]["session_ttl_seconds"] = int(os.environ["SESSION_TTL_SECONDS"])

    db_path = Path(config["storage"]["db_path"])
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    config["storage"]["db_path"] = str(db_path)
    config["llm"]["temperature"] = llm_temperature(config["llm"]["temperature"])
    for field, default in LLM_TIMEOUT_DEFAULTS.items():
        config["llm"][field] = llm_timeout_seconds(config["llm"].get(field, default), field)
    for platform_key in ("xhs", "douyin"):
        config[platform_key] = platform_settings(DEFAULT_CONFIG[platform_key], config[platform_key], platform_key)
    config["sales"] = {"redact_user": boolean(config["sales"]["redact_user"], "sales.redact_user")}
    return config


def load_local_config() -> dict[str, Any]:
    return read_json(local_config_path())


def save_local_config(config: dict[str, Any]) -> None:
    path = local_config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def public_settings(config: dict[str, Any]) -> dict[str, Any]:
    llm = config.get("llm") or {}
    xhs = config.get("xhs") or {}
    platforms = config.get("platforms") or {}
    opencli = config.get("opencli") or {}
    socialdatax = config.get("socialdatax") or {}
    douyin = config.get("douyin") or {}
    sales = config.get("sales") or {}
    socialdatax_env = str(socialdatax.get("api_key_env") or "SOCIALDATAX_API_KEY")
    return {
        "llm": {
            "api_base": llm.get("api_base", ""),
            "model": llm.get("model", ""),
            "temperature": llm_temperature(llm.get("temperature", 0.6)),
            **{
                field: llm_timeout_seconds(llm.get(field, default), field)
                for field, default in LLM_TIMEOUT_DEFAULTS.items()
            },
            "has_api_key": bool(llm.get("api_key") or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")),
        },
        "platforms": {
            "enabled": enabled_platforms(platforms.get("enabled")),
        },
        "opencli": {
            "bin": opencli.get("bin", ""),
        },
        "socialdatax": {
            "api_key_env": socialdatax_env,
            "has_api_key": bool(os.environ.get(socialdatax_env)),
        },
        "xhs": {
            "bin": xhs.get("bin", ""),
            "enable_follow_actions": bool(xhs.get("enable_follow_actions", True)),
            "enable_dm_actions": bool(xhs.get("enable_dm_actions", True)),
        },
        "douyin": {
            "enable_follow_actions": bool(douyin.get("enable_follow_actions", False)),
            "enable_dm_actions": bool(douyin.get("enable_dm_actions", False)),
        },
        "sales": {
            "redact_user": bool(sales.get("redact_user", False)),
        },
        "storage": {
            "backend": "sqlite",
            "db_path": str((config.get("storage") or {}).get("db_path") or ""),
        },
    }


def update_local_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("设置参数必须为对象。")
    for section in ("llm", "xhs", "douyin", "platforms", "opencli", "socialdatax", "sales", "collection"):
        if section in payload and not isinstance(payload[section], dict):
            raise ValueError(f"{section} 设置必须为对象。")
    local_config = deep_merge(copy.deepcopy(DEFAULT_CONFIG), load_local_config())
    llm_payload = payload.get("llm") or {}
    xhs_payload = payload.get("xhs") or {}
    platforms_payload = payload.get("platforms") or {}
    opencli_payload = payload.get("opencli") or {}
    socialdatax_payload = payload.get("socialdatax") or {}
    douyin_payload = payload.get("douyin") or {}
    sales_payload = payload.get("sales") or {}

    llm = local_config.setdefault("llm", {})
    if "api_base" in llm_payload:
        llm["api_base"] = str(llm_payload.get("api_base") or "").strip()
        if llm["api_base"]:
            from llm_client import LLMRequestError, validate_api_base
            try:
                validate_api_base(llm["api_base"])
            except LLMRequestError as exc:
                raise ValueError(str(exc)) from None
    if "model" in llm_payload:
        llm["model"] = str(llm_payload.get("model") or "").strip()
    if "temperature" in llm_payload:
        llm["temperature"] = llm_temperature(llm_payload["temperature"])
    for field, default in LLM_TIMEOUT_DEFAULTS.items():
        value = llm_payload[field] if field in llm_payload else llm.get(field, default)
        llm[field] = llm_timeout_seconds(value, field)
    if llm_payload.get("api_key"):
        llm["api_key"] = str(llm_payload["api_key"]).strip()
    if "clear_api_key" in llm_payload and boolean(llm_payload["clear_api_key"], "llm.clear_api_key"):
        llm["api_key"] = ""

    for platform_key, platform_payload in (("xhs", xhs_payload), ("douyin", douyin_payload)):
        local_config[platform_key] = platform_settings(local_config[platform_key], platform_payload, platform_key)

    platforms = local_config.setdefault("platforms", {})
    if "enabled" in platforms_payload:
        platforms["enabled"] = enabled_platforms(platforms_payload.get("enabled"))

    opencli = local_config.setdefault("opencli", {})
    if "bin" in opencli_payload:
        opencli["bin"] = str(opencli_payload.get("bin") or "").strip()

    socialdatax = local_config.setdefault("socialdatax", {})
    if "api_key_env" in socialdatax_payload:
        api_key_env = str(socialdatax_payload.get("api_key_env") or "SOCIALDATAX_API_KEY").strip() or "SOCIALDATAX_API_KEY"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ValueError("SocialDataX 字段只填写环境变量名，例如 SOCIALDATAX_API_KEY；不要粘贴真实 Key。")
        socialdatax["api_key_env"] = api_key_env

    sales = {"redact_user": local_config["sales"]["redact_user"]}
    if "redact_user" in sales_payload:
        sales["redact_user"] = boolean(sales_payload["redact_user"], "sales.redact_user")
    local_config["sales"] = sales
    if "collection" in payload:
        from agent_harness.planning import normalize_plan
        normalized = normalize_plan({"seedKeywords": ["默认参数"], **payload["collection"]})
        local_config["collection"] = {k: normalized[k] for k in ("maxKeywords", "maxPagesPerKeyword", "maxNotes", "maxCommentsPerNote", "maxCommentsTotal", "delayMin", "delayMax", "timeFilter", "sort", "metric", "readDetail", "scanLeads", "minScore", "platforms")}

    save_local_config(local_config)
    return load_config()


def platform_name() -> str:
    system = platform.system().lower()
    if "windows" in system:
        return "windows"
    if "darwin" in system:
        return "macos"
    return system or "unknown"


def enabled_platforms(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = []
    allowed = {"xhs", "douyin"}
    result = []
    for item in raw_items:
        normalized = item.lower()
        if normalized in allowed and normalized not in result:
            result.append(normalized)
    return result or ["xhs"]
