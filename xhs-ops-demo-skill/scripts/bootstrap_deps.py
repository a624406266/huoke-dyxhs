#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
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


def site_packages_dirs() -> list[Path]:
    if os.name == "nt":
        return [VENV_DIR / "Lib" / "site-packages"]
    return list((VENV_DIR / "lib").glob("python*/site-packages"))


def cleanup_pip_temp_dirs() -> None:
    for site_packages in site_packages_dirs():
        if not site_packages.exists():
            continue
        for child in site_packages.iterdir():
            if not child.name.startswith("~"):
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                print(f"Could not remove stale pip temp path {child}: {exc}", file=sys.stderr)


def install_deps(include_playwright: bool = False) -> None:
    create_venv()
    cleanup_pip_temp_dirs()
    python = str(venv_python())
    expected = requirements_hash(include_playwright)
    current = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    needs_targeted_repair = False
    if current == expected:
        try:
            verify_imports()
            return
        except Exception as exc:
            print(f"Local dependency verification failed, repairing venv: {exc}", file=sys.stderr)
            needs_targeted_repair = True
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "--upgrade", "-r", str(REQUIREMENTS)])
    if needs_targeted_repair:
        run([python, "-m", "pip", "install", "--upgrade", "--force-reinstall", "click>=8.1,<9", "httpx>=0.27,<1"])
    if include_playwright:
        run([python, "-m", "pip", "install", "--upgrade", "playwright>=1.45,<2"])
    STAMP.write_text(expected + "\n", encoding="utf-8")
    verify_imports()


def verify_imports() -> None:
    code = (
        "import click, httpx, xhs_cli, qrcode\n"
        "assert hasattr(click, 'command'), f'broken click module: {getattr(click, \"__file__\", \"<unknown>\")}'\n"
        "print('local dependencies ok')"
    )
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
