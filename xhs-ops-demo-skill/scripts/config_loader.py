from __future__ import annotations

import copy
import json
import os
import platform
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
LEGACY_STORAGE_URL_KEY = "database" + "_url"


DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "xhs": {
        "bin": "",
        "read_delay_min": 7.0,
        "read_delay_max": 60.0,
        "max_count": 20,
        "max_comments_per_note": 50,
        "enable_follow_actions": False,
    },
    "llm": {
        "api_key": "",
        "api_base": "https://poolrouter.com/v1",
        "model": "gemini-3.5-flash",
        "temperature": 0.6,
    },
    "sales": {
        "default_min_score": 60,
        "default_business_direction": "cloud_ai",
        "redact_user": False,
    },
    "storage": {
        "db_path": str(ROOT / "data" / "sales_leads.db"),
    },
    "auth": {
        "admin_username": "admin",
        "admin_password_hash": "",
        "session_ttl_seconds": 86400,
        "sales_users": [],
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
    return json.loads(path.read_text(encoding="utf-8"))


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
    if os.environ.get("HARNESS_ADMIN_PASSWORD_HASH"):
        config["auth"]["admin_password_hash"] = os.environ["HARNESS_ADMIN_PASSWORD_HASH"]
    if os.environ.get("SESSION_TTL_SECONDS"):
        config["auth"]["session_ttl_seconds"] = int(os.environ["SESSION_TTL_SECONDS"])

    (config.get("storage") or {}).pop(LEGACY_STORAGE_URL_KEY, None)
    return config


def load_local_config() -> dict[str, Any]:
    return read_json(local_config_path())


def save_local_config(config: dict[str, Any]) -> None:
    path = local_config_path()
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_settings(config: dict[str, Any]) -> dict[str, Any]:
    llm = config.get("llm") or {}
    xhs = config.get("xhs") or {}
    sales = config.get("sales") or {}
    return {
        "llm": {
            "api_base": llm.get("api_base", ""),
            "model": llm.get("model", ""),
            "temperature": llm.get("temperature", 0.6),
            "has_api_key": bool(llm.get("api_key") or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")),
        },
        "xhs": {
            "bin": xhs.get("bin", ""),
            "read_delay_min": xhs.get("read_delay_min", 7.0),
            "read_delay_max": xhs.get("read_delay_max", 60.0),
            "max_comments_per_note": xhs.get("max_comments_per_note", 50),
            "enable_follow_actions": bool(xhs.get("enable_follow_actions", False)),
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
    local_config = deep_merge(copy.deepcopy(DEFAULT_CONFIG), load_local_config())
    llm_payload = payload.get("llm") or {}
    xhs_payload = payload.get("xhs") or {}
    sales_payload = payload.get("sales") or {}

    llm = local_config.setdefault("llm", {})
    if "api_base" in llm_payload:
        llm["api_base"] = str(llm_payload.get("api_base") or "").strip()
    if "model" in llm_payload:
        llm["model"] = str(llm_payload.get("model") or "").strip()
    if "temperature" in llm_payload:
        llm["temperature"] = float(llm_payload.get("temperature") or 0.6)
    if llm_payload.get("api_key"):
        llm["api_key"] = str(llm_payload["api_key"]).strip()
    if llm_payload.get("clear_api_key"):
        llm["api_key"] = ""

    xhs = local_config.setdefault("xhs", {})
    if "bin" in xhs_payload:
        xhs["bin"] = str(xhs_payload.get("bin") or "").strip()
    if "read_delay_min" in xhs_payload:
        xhs["read_delay_min"] = float(xhs_payload.get("read_delay_min") or 7.0)
    if "read_delay_max" in xhs_payload:
        xhs["read_delay_max"] = float(xhs_payload.get("read_delay_max") or 60.0)
    if "max_comments_per_note" in xhs_payload:
        xhs["max_comments_per_note"] = min(max(int(xhs_payload.get("max_comments_per_note") or 50), 1), 500)
    if "enable_follow_actions" in xhs_payload:
        xhs["enable_follow_actions"] = bool(xhs_payload.get("enable_follow_actions"))

    sales = local_config.setdefault("sales", {})
    if "redact_user" in sales_payload:
        sales["redact_user"] = bool(sales_payload.get("redact_user"))

    local_config.setdefault("storage", {}).pop(LEGACY_STORAGE_URL_KEY, None)
    save_local_config(local_config)
    return load_config()


def platform_name() -> str:
    system = platform.system().lower()
    if "windows" in system:
        return "windows"
    if "darwin" in system:
        return "macos"
    return system or "unknown"
