from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class XhsAdapter:
    def __init__(self, config: dict[str, Any], root: Path, profile_dir: Path | None = None) -> None:
        self.config = config
        self.root = root
        self.profile_dir = profile_dir
        if self.profile_dir:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.command = self.resolve_command()

    def resolve_command(self) -> list[str]:
        configured = str((self.config.get("xhs") or {}).get("bin") or "").strip()
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured).expanduser())

        if platform.system().lower().startswith("win"):
            user_home = Path.home()
            candidates.append(user_home / ".codex" / "skills" / "xiaohongshu-cli" / "scripts" / "xhs.ps1")
        else:
            candidates.extend([
                Path("~/.local/bin/xhs").expanduser(),
                Path("~/.workbuddy/binaries/xhs").expanduser(),
            ])

        for candidate in candidates:
            if candidate.exists():
                if candidate.suffix.lower() == ".ps1":
                    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(candidate)]
                return [str(candidate)]

        path_match = shutil.which("xhs")
        if path_match:
            return [path_match]

        checked = ", ".join(str(c) for c in candidates)
        raise RuntimeError(
            "未找到 xhs CLI。请设置环境变量 XHS_BIN 或在 config.local.json 中配置 xhs.bin。"
            f" 已检查路径：{checked}"
        )

    def run(self, args: list[str], timeout: int = 90) -> dict[str, Any]:
        command = [*self.command, *args]
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        if self.profile_dir:
            home = str(self.profile_dir)
            env.update({
                "HOME": home,
                "USERPROFILE": home,
                "XDG_CONFIG_HOME": str(self.profile_dir / ".config"),
            })
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.root),
                text=True,
                capture_output=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            detail = ""
            if exc.stderr:
                detail = str(exc.stderr)[-500:]
            elif exc.stdout:
                detail = str(exc.stdout)[-500:]
            suffix = f" 原始输出：{detail}" if detail else ""
            raise RuntimeError(f"xhs 本地命令超过 {timeout} 秒未返回，请稍后重试，或使用网页登录/验证后再检查登录态。{suffix}") from exc
        if proc.returncode != 0:
            error_text = (proc.stderr or proc.stdout or "xhs command failed").strip()
            raise RuntimeError(self.normalize_error(error_text[-1600:]))
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"xhs returned non-json output: {proc.stdout[:500]}") from exc

    def normalize_error(self, text: str) -> str:
        lowered = text.lower()
        if self._is_local_runtime_error(lowered):
            return (
                "本地 xhs-cli 依赖环境异常，请重新运行本地工作台启动器以自动修复依赖。"
                "这不是小红书账号或 IP 风控问题。"
                f" 原始错误：{text}"
            )
        if "cookie refresh failed" in lowered or "using existing cookies" in lowered or "age: 7+ days" in lowered:
            return (
                "小红书 Cookie 已过期或刷新失败，请先重新登录后再启动扫描。"
                "建议执行：xhs login --qrcode，或在浏览器登录小红书后重新运行 xhs status。"
                f" 原始错误：{text}"
            )
        if "verification" in lowered or "captcha" in lowered or "滑块" in text or "验证码" in text:
            return f"小红书触发验证码/风控，请先在浏览器或 xhs login 流程中处理后再重试。原始错误：{text}"
        if "unauth" in lowered or "login" in lowered or "未登录" in text or "登录" in text:
            return f"小红书登录态失效，请先执行 xhs login 或 xhs login --qrcode。原始错误：{text}"
        if self._is_xhs_risk_error(lowered, text):
            return f"小红书接口返回风控/IP异常，请暂停任务并人工检查网络环境。原始错误：{text}"
        return text

    def _is_local_runtime_error(self, lowered: str) -> bool:
        markers = [
            "traceback (most recent call last)",
            "modulenotfounderror",
            "importerror",
            "attributeerror",
            "site-packages",
            "no module named",
            "has no attribute",
        ]
        local_modules = ["click", "httpx", "xhs_cli", "qrcode"]
        return any(marker in lowered for marker in markers) and any(module in lowered for module in local_modules)

    def _is_xhs_risk_error(self, lowered: str, text: str) -> bool:
        return (
            "risk" in lowered
            or "ip blocked" in lowered
            or "ip异常" in lowered
            or "ip 异常" in lowered
            or "网络异常" in text
            or "风控" in text
        )

    def status(self, timeout: int = 30) -> dict[str, Any]:
        return self.run(["status", "--json"], timeout=timeout)

    def ensure_authenticated(self) -> None:
        status = self.status()
        if not ((status.get("data") or {}).get("authenticated")):
            raise RuntimeError("小红书未登录。请先执行 xhs login 或 xhs login --qrcode。")

    def search(self, keyword: str, page: int = 1, sort: str = "popular") -> dict[str, Any]:
        if sort not in {"popular", "general", "latest"}:
            raise ValueError("不支持的小红书排序方式。")
        return self.run(["search", keyword, "--sort", sort, "--page", str(page), "--json"], timeout=90)

    def read_note(self, note_id: str) -> dict[str, Any]:
        target = self._note_url(note_id)
        raw = self.run(["read", target, "--json"], timeout=90)
        return self._normalize_detail(raw, str(note_id).strip())

    def comments(self, note_id: str, cursor: str = "") -> dict[str, Any]:
        args = ["comments", self._note_url(note_id), "--json"]
        if cursor:
            args.extend(["--cursor", cursor])
        return self.run(args, timeout=90)

    @staticmethod
    def _note_url(note_id: str) -> str:
        identity = str(note_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            raise ValueError("缺少有效的小红书笔记 ID。")
        # CLI 0.6.4 interprets a numeric bare ID as a listing index. A URL keeps
        # it unambiguous while tokens stay in the CLI's local profile cache.
        return f"https://www.xiaohongshu.com/explore/{identity}"

    @classmethod
    def _normalize_detail(cls, raw: dict[str, Any], expected_id: str) -> dict[str, Any]:
        if raw.get("ok") is False:
            return raw
        data = raw.get("data", raw)
        if not isinstance(data, dict):
            raise ValueError("小红书详情返回格式不受支持。")
        # The feed API returns items[].note_card; the CLI's HTML fallback
        # returns the SSR note itself, with camelCase fields in some versions.
        data = cls._snake_keys(data)
        items = data.get("items") if isinstance(data.get("items"), list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            card = item.get("note_card", item)
            if not isinstance(card, dict):
                continue
            identities = [str(value) for value in (item.get("id"), item.get("note_id"),
                          card.get("id"), card.get("note_id")) if value is not None and str(value)]
            if identities and all(identity == expected_id for identity in identities):
                return {"ok": True, "data": {"items": [{"id": expected_id, "model_type": "note", "note_card": card}]}}
        raise ValueError("小红书详情未返回对应笔记 ID，不能将其他来源作为本条证据。")

    @classmethod
    def _snake_keys(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [cls._snake_keys(item) for item in value]
        if isinstance(value, dict):
            return {re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower(): cls._snake_keys(item)
                    for key, item in value.items()}
        return value
