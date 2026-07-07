#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / ".runtime"
VENV_DIR = RUNTIME_DIR / ".venv"
REQUIREMENTS = ROOT / "requirements-local.txt"
STAMP = RUNTIME_DIR / "requirements-local.sha256"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=str(ROOT), check=True)


def requirements_hash(include_playwright: bool) -> str:
    content = REQUIREMENTS.read_bytes()
    extra = b"\nplaywright" if include_playwright else b""
    return hashlib.sha256(content + extra).hexdigest()


def create_venv() -> None:
    if venv_python().exists():
        return
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)


def install_deps(include_playwright: bool = False) -> None:
    create_venv()
    python = str(venv_python())
    expected = requirements_hash(include_playwright)
    current = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    if current == expected:
        return
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    if include_playwright:
        run([python, "-m", "pip", "install", "playwright>=1.45,<2"])
    STAMP.write_text(expected + "\n", encoding="utf-8")


def verify_imports() -> None:
    code = "import xhs_cli, qrcode; print('local dependencies ok')"
    run([str(venv_python()), "-c", code])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-playwright", action="store_true")
    parser.add_argument("--print-python", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    install_deps(include_playwright=args.with_playwright)
    if args.verify:
        verify_imports()
    if args.print_python:
        print(str(venv_python()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
