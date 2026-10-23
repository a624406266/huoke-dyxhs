from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from source_normalization import to_int
from browser_session import BrowserSession, cdp_ready


DOUYIN_LOGIN_URL = "https://www.douyin.com/"
DOUYIN_REQUIRED_COOKIE_HINTS = ("sessionid", "sid_guard", "passport_csrf_token")


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def first_list(value: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    for key in keys:
        item = value.get(key)
        if isinstance(item, list):
            return item
    for key in ("data", "result", "payload"):
        item = value.get(key)
        found = first_list(item, keys)
        if found:
            return found
    for item in value.values():
        found = first_list(item, keys)
        if found:
            return found
    return []


def tag_names(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = first_text(item.get("name"), item.get("tag_name"), item.get("title"))
            if name:
                names.append(name)
    return names


def normalized_douyin_post(item: dict[str, Any], rank: int = 0) -> dict[str, Any] | None:
    raw = item.get("aweme") if isinstance(item.get("aweme"), dict) else item
    if not isinstance(raw, dict):
        return None
    author = raw.get("author") or raw.get("user") or raw.get("user_info") or {}
    stats = raw.get("statistics") or raw.get("stats") or raw.get("interact_info") or {}
    aweme_id = first_text(raw.get("aweme_id"), raw.get("awemeId"), raw.get("id"), raw.get("item_id"), raw.get("video_id"))
    if not aweme_id:
        return None
    title = first_text(raw.get("desc"), raw.get("description"), raw.get("title"), raw.get("caption"), raw.get("text"), "未命名抖音作品")
    sec_user_id = first_text(
        author.get("sec_uid"),
        author.get("sec_user_id"),
        author.get("secUserId"),
        raw.get("sec_user_id"),
        raw.get("author_sec_user_id"),
    )
    publish_time = raw.get("create_time") or raw.get("createTime") or raw.get("publish_time") or raw.get("publishTime") or 0
    publish_time_ms = 0
    try:
        number = float(publish_time or 0)
        publish_time_ms = int(number if number > 10_000_000_000 else number * 1000)
    except Exception:
        publish_time_ms = 0
    publish_date = ""
    if publish_time_ms:
        try:
            publish_date = time.strftime("%Y-%m-%d", time.localtime(publish_time_ms / 1000.0))
        except Exception:
            publish_date = ""
    liked = to_int(stats.get("digg_count") or stats.get("like_count") or stats.get("liked_count") or raw.get("like_count") or raw.get("liked_count"))
    comments = to_int(stats.get("comment_count") or stats.get("comments") or raw.get("comment_count") or raw.get("comments"))
    shared = to_int(stats.get("share_count") or stats.get("shared_count") or raw.get("share_count") or raw.get("shared_count"))
    collected = to_int(stats.get("collect_count") or stats.get("collected_count") or raw.get("collect_count") or raw.get("collected_count"))
    url = first_text(raw.get("share_url"), raw.get("url"), raw.get("aweme_url"), raw.get("video_url"))
    if not url:
        url = f"https://www.douyin.com/video/{aweme_id}"
    return {
        "platform": "douyin",
        "model_type": "note",
        "id": aweme_id,
        "aweme_id": aweme_id,
        "post_id": aweme_id,
        "note_card": {
            "note_id": aweme_id,
            "display_title": title,
            "desc": title,
            "interact_info": {
                "liked_count": liked,
                "collected_count": collected,
                "comment_count": comments,
                "share_count": shared,
            },
            "user": {
                "nickname": first_text(author.get("nickname"), author.get("name"), author.get("short_id"), raw.get("author_name")),
                "user_id": sec_user_id or first_text(author.get("uid"), author.get("id"), raw.get("author_user_id"), raw.get("author_douyin_id")),
                "sec_user_id": sec_user_id,
            },
            "tag_list": [{"name": tag} for tag in tag_names(raw.get("cha_list") or raw.get("topic_tags"))],
            "time": publish_time_ms,
            "url": url,
            "raw": raw,
        },
        "rank": rank,
        "url": url,
    }


def normalized_douyin_comment(item: dict[str, Any], aweme_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    user = item.get("user") or item.get("user_info") or item.get("author") or {}
    raw_comment_id = first_text(item.get("cid"), item.get("comment_id"), item.get("commentId"), item.get("id"))
    content = first_text(item.get("text"), item.get("content"), item.get("comment_text"), item.get("desc"))
    if not raw_comment_id and not content:
        return None
    sec_user_id = first_text(
        user.get("sec_uid"),
        user.get("sec_user_id"),
        user.get("secUserId"),
        item.get("sec_user_id"),
        item.get("author_sec_user_id"),
    )
    return {
        "platform": "douyin",
        "id": f"douyin:{raw_comment_id}" if raw_comment_id else "",
        "source_comment_id": f"douyin:{raw_comment_id}" if raw_comment_id else "",
        "id_is_synthetic": not bool(raw_comment_id),
        "raw_comment_id": raw_comment_id,
        "aweme_id": aweme_id,
        "content": content,
        "ip_location": first_text(item.get("ip_label"), item.get("ip_location"), item.get("ip"), item.get("location")),
        "user_info": {
            "nickname": first_text(user.get("nickname"), user.get("name"), user.get("short_id"), item.get("author_name")),
            "user_id": sec_user_id or first_text(user.get("uid"), user.get("id"), item.get("author_user_id"), item.get("author_douyin_id")),
            "sec_user_id": sec_user_id,
        },
        "create_time": item.get("create_time") or item.get("createTime"),
        "raw": item,
    }


class DouyinReadAdapter:
    platform = "douyin"

    def __init__(self, config: dict[str, Any], root: Path) -> None:
        self.config = config
        self.root = root
        self.command = self.resolve_command()

    def resolve_command(self) -> list[str]:
        configured = str((self.config.get("opencli") or {}).get("bin") or os.environ.get("OPENCLI_BIN") or "").strip()
        if configured:
            return [str(Path(configured).expanduser())]
        found = shutil.which("opencli")
        return [found] if found else ["opencli"]

    def api_key_env(self) -> str:
        return str((self.config.get("socialdatax") or {}).get("api_key_env") or "SOCIALDATAX_API_KEY").strip() or "SOCIALDATAX_API_KEY"

    def status(self, timeout: int = 30) -> dict[str, Any]:
        command_found = bool(shutil.which(self.command[0]) or Path(self.command[0]).exists())
        api_key_env = self.api_key_env()
        return {
            "ok": command_found and bool(os.environ.get(api_key_env)),
            "platform": "douyin",
            "command": self.command,
            "commandFound": command_found,
            "apiKeyEnv": api_key_env,
            "hasApiKey": bool(os.environ.get(api_key_env)),
            "readOnly": True,
        }

    def ensure_authenticated(self) -> None:
        status = self.status()
        if not status["commandFound"]:
            raise RuntimeError("未找到 opencli。请安装 OpenCLI，并设置 OPENCLI_BIN 或在配置中填写 opencli.bin。")
        if not status["hasApiKey"]:
            raise RuntimeError(f"未配置 SocialDataX API Key。请设置环境变量 {status['apiKeyEnv']} 后再扫描抖音。")

    def run(self, args: list[str], timeout: int = 90) -> Any:
        self.ensure_authenticated()
        proc = subprocess.run(
            [*self.command, "socialdatax", *args, "-f", "json"],
            cwd=str(self.root),
            text=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        if proc.returncode != 0:
            error_text = (proc.stderr or proc.stdout or "opencli socialdatax command failed").strip()
            raise RuntimeError(self.normalize_error(error_text[-1600:]))
        return self.loads_json(proc.stdout)

    def loads_json(self, text: str) -> Any:
        cleaned = (text or "").strip()
        if not cleaned:
            return {}
        for start in ("{", "["):
            index = cleaned.find(start)
            if index >= 0:
                try:
                    return json.loads(cleaned[index:])
                except json.JSONDecodeError:
                    pass
        raise RuntimeError(f"opencli returned non-json output: {cleaned[:500]}")

    def normalize_error(self, text: str) -> str:
        lowered = text.lower()
        if "api" in lowered and "key" in lowered:
            return f"SocialDataX API Key 无效或未配置，请检查 {self.api_key_env()}。原始错误：{text}"
        if "not found" in lowered or "enoent" in lowered:
            return "未找到 opencli 或 SocialDataX 插件。请执行 opencli plugin install github:DevinChen2014/opencli-plugin-socialdatax。"
        return text

    def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        if isinstance(page, bool) or not isinstance(page, int) or page != 1:
            raise ValueError("抖音读取接口尚未支持分页，不能将后续页视为已采集；请将每个关键词页数设为 1。")
        data = self.run(["douyin-search", "--keyword", keyword], timeout=90)
        items = first_list(data, ("items", "works", "aweme_list", "awemeList", "list", "data_list", "videos"))
        normalized = [post for idx, item in enumerate(items, 1) if isinstance(item, dict) for post in [normalized_douyin_post(item, idx)] if post]
        return {"data": {"items": normalized, "raw": data}}

    def read_note(self, note_id: str) -> dict[str, Any]:
        data = self.run(["douyin-detail", "--aweme-id", str(note_id)], timeout=90)
        item = data if isinstance(data, dict) else {}
        candidates = first_list(data, ("items", "works", "aweme_list", "list"))
        if candidates and isinstance(candidates[0], dict):
            item = candidates[0]
        normalized = normalized_douyin_post(item, 1)
        return {"data": {"items": [normalized] if normalized else [], "raw": data}}

    def comments(self, note_id: str, cursor: str = "") -> dict[str, Any]:
        args = ["douyin-comments", "--aweme-id", str(note_id)]
        if cursor:
            args.extend(["--page-token", cursor])
        data = self.run(args, timeout=90)
        items = first_list(data, ("comments", "comment_list", "commentList", "items", "list", "data_list"))
        normalized = [comment for item in items if isinstance(item, dict) for comment in [normalized_douyin_comment(item, str(note_id))] if comment]
        page_data = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data if isinstance(data, dict) else {}
        next_token = first_text(
            page_data.get("next_page_token") if isinstance(page_data, dict) else "",
            page_data.get("nextPageToken") if isinstance(page_data, dict) else "",
            page_data.get("cursor") if isinstance(page_data, dict) else "",
        )
        has_more = bool((page_data or {}).get("has_more") or (page_data or {}).get("hasMore") or next_token)
        return {"data": {"comments": normalized, "cursor": next_token, "has_more": has_more, "raw": data}}


class DouyinLoginHelper(BrowserSession):
    def __init__(self, root: Path, home_dir: Path | None = None) -> None:
        self.root = root
        self.home_dir = home_dir or root / "data" / "douyin-default-profile"
        super().__init__(self.home_dir / "chrome-profile", (9330, 9429))
        self.mode = "idle"

    def start(self, mode: str | None = None) -> dict[str, Any]:
        return self._start_browser()

    def _start_browser(self) -> dict[str, Any]:
        with self.lock:
            self.mode = "idle"
            self.start_browser(DOUYIN_LOGIN_URL)
            self.mode = "browser"
            return self._session_payload("running", "抖音登录/验证窗口已打开，请在 Chrome 中完成操作。")

    def status(self) -> dict[str, Any]:
        if not self.port:
            return {"running": False, "mode": self.mode, "message": "抖音登录/验证窗口未启动。"}
        ready = cdp_ready(self.port)
        return self._session_payload("running" if ready else "closed", "抖音登录/验证窗口运行中。" if ready else "抖音登录/验证窗口已关闭。")

    def confirm(self) -> dict[str, Any]:
        if not self.port or not cdp_ready(self.port):
            raise RuntimeError("抖音登录窗口未启动或已关闭，请先点击“打开抖音登录/验证窗口”。")
        data = self._run_script(self._status_script(), [])
        if not data.get("ok"):
            raise RuntimeError(data.get("message") or "尚未确认抖音登录态，请在 Chrome 窗口完成登录/验证后再点一次。")
        return data

    def reset(self) -> dict[str, Any]:
        removed: list[str] = []
        remaining: list[str] = []
        with self.lock:
            self.stop_browser()
            self.mode = "idle"
            if self.profile_dir.exists():
                shutil.rmtree(self.profile_dir, ignore_errors=True)
                if self.profile_dir.exists():
                    remaining.append(str(self.profile_dir))
                else:
                    removed.append(str(self.profile_dir))
            self.home_dir.mkdir(parents=True, exist_ok=True)
        return {
            "ok": not remaining,
            "mode": "idle",
            "status": "cleared" if not remaining else "partial",
            "message": "已清除本机抖音登录态。请重新打开网页登录/验证。" if not remaining else "部分抖音登录缓存被 Chrome 占用，请先关闭验证窗口。",
            "removed": removed,
            "remaining": remaining,
            "homeDir": str(self.home_dir),
        }

    def _session_payload(self, status: str, message: str) -> dict[str, Any]:
        return {
            "status": status,
            "mode": self.mode,
            "message": message,
            "loginUrl": DOUYIN_LOGIN_URL,
            "port": self.port,
            "profileDir": str(self.profile_dir),
            "homeDir": str(self.home_dir),
            "requiredCookieHints": list(DOUYIN_REQUIRED_COOKIE_HINTS),
        }

    def _run_script(self, script: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
        if not self.port:
            raise RuntimeError("抖音登录窗口未启动。")
        proc = subprocess.run(
            [str(self._python()), "-c", script, str(self.port), *args],
            cwd=str(self.root),
            text=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        output = (proc.stdout or "").strip()
        if proc.returncode != 0 and not output:
            raise RuntimeError((proc.stderr or "抖音浏览器动作执行失败").strip()[-1000:])
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"抖音浏览器动作返回非 JSON：{output[:500]}") from exc

    def _python(self) -> Path:
        if platform.system().lower().startswith("win"):
            candidate = self.root / ".runtime" / ".venv" / "Scripts" / "python.exe"
        else:
            candidate = self.root / ".runtime" / ".venv" / "bin" / "python"
        return candidate if candidate.exists() else Path(sys.executable)

    def _status_script(self) -> str:
        required = json.dumps(DOUYIN_REQUIRED_COOKIE_HINTS)
        return f"""
import json
import sys
from playwright.sync_api import sync_playwright
port = int(sys.argv[1])
required = tuple({required})
try:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{{port}}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("{DOUYIN_LOGIN_URL}", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1200)
        except Exception:
            pass
        text = ""
        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            pass
        session = browser.new_browser_cdp_session()
        raw = session.send("Storage.getCookies").get("cookies", [])
        cookies = {{}}
        for item in raw:
            domain = item.get("domain") or ""
            name = item.get("name") or ""
            value = item.get("value") or ""
            if "douyin.com" in domain and name and value:
                cookies[name] = value
        cookie_hit = any(cookies.get(name) for name in required)
        login_markers = any(k in text for k in ["消息", "私信", "创作者服务中心", "发布作品", "我的"])
        shows_login = "登录" in text and not login_markers
        ok = bool(cookie_hit or login_markers) and not shows_login
        print(json.dumps({{
            "ok": ok,
            "platform": "douyin",
            "url": page.url,
            "cookie_count": len(cookies),
            "cookie_names": sorted(cookies.keys()),
            "required_present": {{name: bool(cookies.get(name)) for name in required}},
            "page_markers": {{"shows_login": shows_login, "login_markers": login_markers}},
            "message": "抖音登录态已确认。" if ok else "尚未确认抖音登录态，请在 Chrome 窗口完成登录或验证码验证。",
        }}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{"ok": False, "platform": "douyin", "error": f"{{type(exc).__name__}}: {{exc}}", "message": "抖音登录态检查失败。"}}, ensure_ascii=False))
"""



class DouyinBrowserActionAdapter:
    """Platform binding for the verified browser action contract."""
    platform = "douyin"

    def __init__(self, helper: DouyinLoginHelper) -> None:
        self.helper = helper
        from xhs_browser_actions import XhsBrowserActionAdapter
        self._browser = XhsBrowserActionAdapter(helper, platform="douyin")

    def ensure_authenticated(self) -> None:
        data = self.helper.confirm()
        if not data.get("ok"):
            raise RuntimeError(data.get("message") or data.get("error") or "尚未确认抖音登录态。")

    def account_identity(self) -> dict[str, str]:
        return self._browser.account_identity()

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        return self._browser.execute(action)
