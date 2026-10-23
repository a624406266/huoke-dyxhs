"""Lifecycle of a Chrome process owned by one local platform profile."""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def find_free_port(start: int, end: int) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("未找到可用 Chrome 调试端口。")


def chrome_executable() -> Path:
    system = platform.system().lower()
    if system.startswith("win"):
        candidates = [Path(os.environ[name]) / "Google" / "Chrome" / "Application" / "chrome.exe"
                      for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
                      if os.environ.get(name)]
    elif system == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                      Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        candidates = [Path(path) for path in ("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("未找到 Google Chrome，请先安装 Chrome 后再使用网页登录助手。")


def cdp_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


class BrowserSession:
    def __init__(self, profile_dir: Path, port_range: tuple[int, int]) -> None:
        self.profile_dir = profile_dir
        self.port_range = port_range
        self.port: int | None = None
        self.process: subprocess.Popen[Any] | None = None
        self.lock = threading.RLock()

    def start_browser(self, url: str) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None and self.port and cdp_ready(self.port):
                return
            self.stop_browser()
            chrome = chrome_executable()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            port = find_free_port(*self.port_range)
            process = subprocess.Popen([
                str(chrome), f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self.profile_dir}", "--new-window", "--no-first-run",
                "--disable-default-apps", url,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.port, self.process = port, process
            try:
                for _ in range(30):
                    if process.poll() is not None:
                        raise RuntimeError("Chrome 登录窗口已退出，未能建立调试连接。")
                    if cdp_ready(port):
                        return
                    time.sleep(0.5)
                raise RuntimeError("Chrome 登录窗口调试端口未就绪，已停止本次启动，请重试。")
            except BaseException:
                self.stop_browser()
                raise

    def stop_browser(self) -> None:
        with self.lock:
            if self.process is not None:
                if self.process.poll() is None:
                    try:
                        self.process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                else:
                    self.process.wait(timeout=5)
            # Keep the handle if termination failed so reset can retry without
            # deleting a profile still owned by a running browser.
            self.process, self.port = None, None
