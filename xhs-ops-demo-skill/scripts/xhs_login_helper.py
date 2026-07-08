from __future__ import annotations

import base64
from io import BytesIO
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


LOGIN_URL = "https://www.xiaohongshu.com/explore"
REQUIRED_COOKIES = ("a1", "webId", "web_session")


def find_free_port(start: int = 9223, end: int = 9323) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("未找到可用 Chrome 调试端口。")


def find_existing_cdp_port(start: int = 9223, end: int = 9323) -> int | None:
    for port in range(start, end + 1):
        if cdp_ready(port):
            return port
    return None


def chrome_executable() -> Path:
    system = platform.system().lower()
    candidates: list[Path]
    if "windows" in system:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif "darwin" in system:
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        ]
    else:
        candidates = [Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser")]

    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise RuntimeError("未找到 Google Chrome，请先安装 Chrome 后再使用网页登录助手。")


def xhs_python() -> Path:
    if platform.system().lower().startswith("win"):
        candidate = Path.home() / ".codex" / "tools" / "xiaohongshu-cli" / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = Path.home() / ".codex" / "tools" / "xiaohongshu-cli" / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


def cdp_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


class XhsLoginHelper:
    def __init__(self, root: Path, home_dir: Path | None = None) -> None:
        self.root = root
        self.home_dir = home_dir or root / "data" / "xhs-default-profile"
        self.profile_dir = self.home_dir / "chrome-profile"
        self.port: int | None = None
        self.process: subprocess.Popen[Any] | None = None
        self.lock = threading.Lock()
        self.mode = "idle"
        self.qr_client: Any | None = None
        self.qr_session: dict[str, Any] = {}

    def start(self, mode: str | None = None) -> dict[str, Any]:
        login_mode = str(mode or os.environ.get("XHS_LOGIN_MODE") or "qrcode").strip().lower()
        if login_mode != "browser":
            return self._start_qrcode()
        return self._start_browser()

    def _start_browser(self) -> dict[str, Any]:
        if self.port and cdp_ready(self.port):
            self.mode = "browser"
            return self._session_payload("running", "登录/验证窗口已打开，请在 Chrome 中完成操作。")

        existing_port = find_existing_cdp_port()
        if existing_port:
            self.port = existing_port
            self._open_url_in_chrome()
            self.mode = "browser"
            return self._session_payload("running", "已连接现有 Chrome 调试会话，并打开小红书页面。")

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.port = find_free_port(9230, 9323)
        chrome = chrome_executable()
        args = [
            str(chrome),
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.profile_dir}",
            "--new-window",
            "--no-first-run",
            "--disable-default-apps",
            LOGIN_URL,
        ]
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if cdp_ready(self.port):
                self.mode = "browser"
                return self._session_payload("running", "已打开小红书登录/验证窗口。")
            time.sleep(0.5)
        raise RuntimeError("Chrome 登录窗口已尝试启动，但调试端口未就绪。请关闭窗口后重试。")

    def _start_qrcode(self) -> dict[str, Any]:
        try:
            from xhs_cli.client import XhsClient
            from xhs_cli.qr_login import _apply_session_cookies, _generate_a1, _generate_webid
        except ImportError as exc:
            raise RuntimeError("二维码登录需要在本地 Python 环境安装 xiaohongshu-cli。请重新运行本地启动器完成依赖安装。") from exc

        with self.lock:
            self._close_qr_client_locked()
            self.home_dir.mkdir(parents=True, exist_ok=True)
            a1 = _generate_a1()
            webid = _generate_webid()
            client = XhsClient({"a1": a1, "webId": webid}, request_delay=0)
            try:
                activate_data = client.login_activate()
                _apply_session_cookies(client, activate_data)
            except Exception:
                pass
            qr_data = client.create_qr_login()
            qr_url = str(qr_data.get("url") or "").strip()
            if not qr_url:
                client.close()
                raise RuntimeError(f"小红书二维码创建失败：{qr_data}")
            self.qr_client = client
            self.mode = "qrcode"
            self.qr_session = {
                "a1": a1,
                "webid": webid,
                "qr_id": str(qr_data.get("qr_id") or ""),
                "code": str(qr_data.get("code") or ""),
                "qr_url": qr_url,
                "created_at": time.time(),
                "last_status": 0,
            }
            return self._qrcode_payload("waiting", "请使用小红书 App 扫描二维码并在手机上确认登录。")

    def _close_qr_client_locked(self) -> None:
        if self.qr_client is not None:
            try:
                self.qr_client.close()
            except Exception:
                pass
        self.qr_client = None
        self.qr_session = {}

    def reset(self) -> dict[str, Any]:
        removed: list[str] = []
        remaining: list[str] = []
        with self.lock:
            self._close_qr_client_locked()
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
            self.process = None
            self.port = None
            self.mode = "idle"

            for path in (self.home_dir / ".xiaohongshu-cli", self.profile_dir):
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                    if path.exists():
                        remaining.append(str(path))
                    else:
                        removed.append(str(path))

            self.home_dir.mkdir(parents=True, exist_ok=True)

        if remaining:
            message = "部分小红书登录缓存被 Chrome 占用，请先关闭工作台打开的 Chrome 验证窗口，再点击“切换账号”。"
            status = "partial"
        else:
            message = "已清除本机小红书登录态。请重新扫码或打开网页登录/验证以切换账号。"
            status = "cleared"
        return {
            "ok": not remaining,
            "mode": "idle",
            "status": status,
            "message": message,
            "removed": removed,
            "remaining": remaining,
            "homeDir": str(self.home_dir),
        }

    def confirm(self) -> dict[str, Any]:
        if self.mode == "qrcode":
            return self._confirm_qrcode()
        return self._confirm_browser()

    def _confirm_qrcode(self) -> dict[str, Any]:
        try:
            from xhs_cli.qr_login import QR_CONFIRMED, QR_SCANNED, _build_saved_cookies, _complete_confirmed_session, _resolved_user_id
        except ImportError as exc:
            raise RuntimeError("二维码登录需要在本地 Python 环境安装 xiaohongshu-cli。请重新运行本地启动器完成依赖安装。") from exc

        with self.lock:
            if not self.qr_client or not self.qr_session:
                raise RuntimeError("二维码登录会话未启动或已过期，请重新创建二维码。")
            if time.time() - float(self.qr_session.get("created_at") or 0) > 240:
                self._close_qr_client_locked()
                raise RuntimeError("二维码已过期，请重新创建。")
            status_data = self.qr_client.check_qr_status(self.qr_session["qr_id"], self.qr_session["code"])
            code_status = int(status_data.get("codeStatus", -1))
            self.qr_session["last_status"] = code_status
            if code_status == QR_SCANNED:
                raise RuntimeError("已扫码，请在手机小红书中确认登录。")
            if code_status != QR_CONFIRMED:
                raise RuntimeError("等待扫码确认中，请扫码后再点击完成验证。")
            confirmed_user_id = str(status_data.get("userId") or "").strip()
            if not confirmed_user_id:
                raise RuntimeError("二维码已确认，但未返回用户 ID，请重新创建二维码。")
            try:
                completion_data = _complete_confirmed_session(
                    self.qr_client,
                    self.qr_session["qr_id"],
                    self.qr_session["code"],
                    confirmed_user_id,
                )
            except Exception as exc:
                error_text = str(exc)
                if "Captcha required" in error_text or "NeedVerify" in type(exc).__name__ or "验证码" in error_text:
                    self._close_qr_client_locked()
                    self.mode = "idle"
                    raise RuntimeError(
                        "手机端已确认扫码，但小红书要求继续完成网页登录/验证码验证。"
                        "请点击“打开网页登录/验证”，在打开的 Chrome 窗口完成验证后，再点击“完成后验证登录态”。"
                        f" 原始错误：{error_text}"
                    ) from exc
                raise
            user_id = _resolved_user_id(completion_data) or confirmed_user_id
            cookies = _build_saved_cookies(self.qr_session["a1"], self.qr_session["webid"], self.qr_client.cookies)
            cookie_path = self._save_cookies(cookies)
            self._close_qr_client_locked()
            self.mode = "idle"
            return {
                "ok": True,
                "mode": "qrcode",
                "userId": user_id,
                "cookie_count": len(cookies),
                "cookie_names": sorted(cookies.keys()),
                "saved_to": str(cookie_path),
                "homeDir": str(self.home_dir),
            }

    def _confirm_browser(self) -> dict[str, Any]:
        if not self.port or not cdp_ready(self.port):
            raise RuntimeError("登录窗口未启动或已关闭，请先点击“打开小红书登录/验证窗口”。")
        script = self._extract_script()
        proc = subprocess.run(
            [str(xhs_python()), "-c", script, str(self.port)],
            cwd=str(self.root),
            text=True,
            capture_output=True,
            timeout=45,
            encoding="utf-8",
            errors="replace",
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "HOME": str(self.home_dir),
                "USERPROFILE": str(self.home_dir),
                "XDG_CONFIG_HOME": str(self.home_dir / ".config"),
            },
        )
        output = (proc.stdout or "").strip()
        if proc.returncode != 0 and not output:
            error_text = (proc.stderr or proc.stdout or "Cookie 导出失败").strip()
            if "ECONNREFUSED" in error_text or "connect_over_cdp" in error_text:
                raise RuntimeError("无法连接登录窗口调试端口。请重新点击“打开小红书登录/验证窗口”。")
            raise RuntimeError(error_text[-1000:])
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Cookie 导出返回非 JSON：{output[:500]}") from exc
        if data.get("error"):
            raise RuntimeError(f"登录窗口读取失败：{data['error']}。请重新打开登录窗口后再试。")
        if not data.get("ok"):
            missing = "、".join(data.get("missing") or [])
            raise RuntimeError(f"尚未拿到完整登录 Cookie，缺少：{missing or '未知'}。请在 Chrome 窗口完成登录/验证后再点一次。")
        return data

    def status(self) -> dict[str, Any]:
        if self.mode == "qrcode":
            return self._qrcode_status()
        if not self.port:
            return {"running": False, "message": "登录/验证窗口未启动。"}
        return self._session_payload("running" if cdp_ready(self.port) else "closed", "登录/验证窗口运行中。" if cdp_ready(self.port) else "登录/验证窗口已关闭。")

    def _qrcode_status(self) -> dict[str, Any]:
        try:
            from xhs_cli.qr_login import QR_CONFIRMED, QR_SCANNED
        except ImportError:
            return {"status": "error", "message": "本地环境未安装 xiaohongshu-cli。请重新运行本地启动器完成依赖安装。", "mode": "qrcode"}
        with self.lock:
            if not self.qr_client or not self.qr_session:
                return {"status": "closed", "message": "二维码登录会话未启动。", "mode": "qrcode"}
            if time.time() - float(self.qr_session.get("created_at") or 0) > 240:
                self._close_qr_client_locked()
                return {"status": "expired", "message": "二维码已过期，请重新创建。", "mode": "qrcode"}
            try:
                status_data = self.qr_client.check_qr_status(self.qr_session["qr_id"], self.qr_session["code"])
                code_status = int(status_data.get("codeStatus", -1))
                self.qr_session["last_status"] = code_status
            except Exception as exc:
                return {**self._qrcode_payload("error", f"二维码状态检查失败：{exc}"), "mode": "qrcode"}
            if code_status == QR_CONFIRMED:
                return self._qrcode_payload("confirmed", "手机端已确认，请点击“完成后验证登录态”。")
            if code_status == QR_SCANNED:
                return self._qrcode_payload("scanned", "已扫码，请在手机小红书中确认登录。")
            return self._qrcode_payload("waiting", "等待扫码。")

    def _session_payload(self, status: str, message: str) -> dict[str, Any]:
        return {
            "status": status,
            "mode": self.mode,
            "message": message,
            "loginUrl": LOGIN_URL,
            "port": self.port,
            "profileDir": str(self.profile_dir),
            "homeDir": str(self.home_dir),
            "requiredCookies": list(REQUIRED_COOKIES),
        }

    def _qrcode_payload(self, status: str, message: str) -> dict[str, Any]:
        qr_url = str(self.qr_session.get("qr_url") or "")
        return {
            "status": status,
            "mode": "qrcode",
            "message": message,
            "qrUrl": qr_url,
            "qrImage": self._qr_image(qr_url),
            "expiresAt": float(self.qr_session.get("created_at") or time.time()) + 240,
            "homeDir": str(self.home_dir),
            "requiredCookies": list(REQUIRED_COOKIES),
        }

    def _qr_image(self, qr_url: str) -> str:
        if not qr_url:
            return ""
        try:
            import qrcode
        except ImportError:
            return ""
        image = qrcode.make(qr_url)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def _cookie_path(self) -> Path:
        return self.home_dir / ".xiaohongshu-cli" / "cookies.json"

    def _save_cookies(self, cookies: dict[str, str]) -> Path:
        cookie_path = self._cookie_path()
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**cookies, "saved_at": time.time()}
        cookie_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            cookie_path.chmod(0o600)
        except OSError:
            pass
        return cookie_path

    def _open_url_in_chrome(self) -> None:
        try:
            subprocess.Popen([str(chrome_executable()), LOGIN_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _extract_script(self) -> str:
        required = json.dumps(REQUIRED_COOKIES)
        return f"""
import json
import sys
from playwright.sync_api import sync_playwright
from xhs_cli.cookies import save_cookies, get_cookie_path

port = int(sys.argv[1])
required = tuple({required})
try:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{{port}}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("{LOGIN_URL}", wait_until="domcontentloaded", timeout=15000)
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
            if "xiaohongshu.com" in domain and name and value:
                cookies[name] = value
        missing = [name for name in required if not cookies.get(name)]
        result = {{
            "ok": not missing,
            "url": page.url,
            "cookie_count": len(cookies),
            "cookie_names": sorted(cookies.keys()),
            "required_present": {{name: bool(cookies.get(name)) for name in required}},
            "missing": missing,
            "page_markers": {{
                "shows_login": "登录" in text,
                "shows_notifications": "通知" in text,
                "shows_publish": "发布" in text,
                "shows_profile_hint": any(k in text for k in ["我", "我的", "私信", "创作中心"]),
            }},
        }}
        if not missing:
            save_cookies(cookies)
            result["saved_to"] = str(get_cookie_path())
        print(json.dumps(result, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}", "missing": list(required)}}, ensure_ascii=False))
"""
