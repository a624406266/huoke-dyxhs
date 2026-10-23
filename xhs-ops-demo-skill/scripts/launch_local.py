#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parent.parent


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def health_ok(host: str, port: int) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/api/config", timeout=1) as resp:
            data = json.loads(resp.read())
            return resp.status == 200 and data.get("ok") is True and (data.get("data") or {}).get("app") == "keyword-workbench"
    except Exception:
        return False


def run_bootstrap() -> Path:
    cmd = [sys.executable, str(ROOT / "scripts" / "bootstrap_deps.py"), "--print-python"]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=True)
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("依赖准备完成，但没有返回本地 Python 路径。")
    return Path(lines[-1])


def write_local_config(host: str, port: int) -> None:
    path = ROOT / "config.local.json"
    if path.exists():
        return
    example_path = ROOT / "config.example.json"
    config = json.loads(example_path.read_text(encoding="utf-8")) if example_path.exists() else {}
    config.setdefault("server", {})
    config["server"]["host"] = host
    config["server"]["port"] = port
    config.setdefault("storage", {})
    config["storage"]["db_path"] = str(ROOT / "data" / "keyword_workbench_v3.db")
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Created config.local.json")


def find_xhs_bin(venv_python: Path) -> str:
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend([
            venv_python.parent / "xhs.exe",
            Path.home() / ".codex" / "skills" / "xiaohongshu-cli" / "scripts" / "xhs.ps1",
        ])
    else:
        candidates.extend([
            venv_python.parent / "xhs",
            Path.home() / ".local" / "bin" / "xhs",
            Path.home() / ".workbuddy" / "binaries" / "xhs",
        ])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    host = args.host
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("本地单人版只允许本机地址。")
    port = args.port
    url = f"http://{host}:{port}/index.html"

    if health_ok(host, port):
        print(f"Open lead workbench is already running: {url}")
        if not args.no_open:
            webbrowser.open(url)
        return 0
    if is_port_open(host, port):
        raise RuntimeError(f"端口 {host}:{port} 已被占用，但不是本地工作台服务。")

    venv_python = run_bootstrap()
    write_local_config(host, port)

    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HOST": host,
        "PORT": str(port),
        "XHS_LOGIN_MODE": os.environ.get("XHS_LOGIN_MODE", "qrcode"),
    }
    if not env.get("XHS_BIN"):
        detected_xhs = find_xhs_bin(venv_python)
        if detected_xhs:
            env["XHS_BIN"] = detected_xhs
            print(f"Using XHS_BIN={detected_xhs}")

    proc = subprocess.Popen([str(venv_python), str(ROOT / "scripts" / "server.py")], cwd=str(ROOT), env=env)
    try:
        for _ in range(80):
            if health_ok(host, port):
                print(f"Open lead workbench running: {url}")
                if not args.no_open:
                    webbrowser.open(url)
                return proc.wait()
            if proc.poll() is not None:
                return int(proc.returncode or 1)
            time.sleep(0.25)
        raise RuntimeError("本地服务启动超时。")
    except KeyboardInterrupt:
        return 130
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
