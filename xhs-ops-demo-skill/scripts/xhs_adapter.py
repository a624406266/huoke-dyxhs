from __future__ import annotations

import json
import os
import platform
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
        if proc.returncode != 0:
            error_text = (proc.stderr or proc.stdout or "xhs command failed").strip()
            raise RuntimeError(self.normalize_error(error_text[-1600:]))
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"xhs returned non-json output: {proc.stdout[:500]}") from exc

    def normalize_error(self, text: str) -> str:
        lowered = text.lower()
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
        if "ip" in lowered or "risk" in lowered or "风控" in text:
            return f"小红书接口返回风控/IP异常，请暂停任务并人工检查网络环境。原始错误：{text}"
        return text

    def status(self) -> dict[str, Any]:
        return self.run(["status", "--json"], timeout=30)

    def ensure_authenticated(self) -> None:
        status = self.status()
        if not ((status.get("data") or {}).get("authenticated")):
            raise RuntimeError("小红书未登录。请先执行 xhs login 或 xhs login --qrcode。")

    def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        return self.run(["search", keyword, "--sort", "popular", "--page", str(page), "--json"], timeout=90)

    def read_note(self, note_id: str) -> dict[str, Any]:
        return self.run(["read", note_id, "--json"], timeout=90)

    def comments(self, note_id: str) -> dict[str, Any]:
        return self.run(["comments", note_id, "--json"], timeout=90)
