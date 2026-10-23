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
# Python security announcement, 2026-10-20. The fixed wheel hash also protects
# this first upgrade when the old pip still trusts the compromised CA.
MIN_PIP_VERSION = "26.2.2"
MIN_CERTIFI_VERSION = "2026.10.19"
SECURE_PIP_REQUIREMENT = (
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/"
    "pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=str(ROOT), check=True)


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def create_venv() -> None:
    if venv_python().exists():
        return
    if sys.version_info < (3, 10):
        raise RuntimeError("首次安装需要 Python 3.10 或更高版本，请通过 PYTHON_BIN 指定；不会修改系统 Python。")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)


def has_secure_pip() -> bool:
    code = (
        "from importlib.metadata import version\n"
        "from pip._vendor.packaging.version import Version\n"
        f"raise SystemExit(0 if Version(version('pip')) >= Version('{MIN_PIP_VERSION}') else 1)"
    )
    result = subprocess.run([str(venv_python()), "-I", "-c", code],
                            capture_output=True, check=False)
    return result.returncode == 0


def ensure_secure_pip() -> None:
    if has_secure_pip():
        return
    print(f"Updating project pip to {MIN_PIP_VERSION} using a SHA-256 verified wheel.")
    run([str(venv_python()), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
         "--no-index", "--no-deps", "--no-cache-dir", SECURE_PIP_REQUIREMENT])
    if not has_secure_pip():
        raise RuntimeError("项目 pip 安全版本校验失败，已停止安装业务依赖。")


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


def install_deps() -> None:
    create_venv()
    ensure_secure_pip()
    cleanup_pip_temp_dirs()
    python = str(venv_python())
    expected = requirements_hash()
    current = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    needs_targeted_repair = False
    if current == expected:
        try:
            verify_imports()
            return
        except Exception as exc:
            print(f"Local dependency verification failed, repairing venv: {exc}", file=sys.stderr)
            needs_targeted_repair = True
    run([python, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    if needs_targeted_repair:
        run([python, "-m", "pip", "install", "--upgrade", "--force-reinstall", "click>=8.1,<9", "httpx>=0.27,<1"])
    verify_imports()
    STAMP.write_text(expected + "\n", encoding="utf-8")


def verify_imports() -> None:
    code = (
        "import click, httpx, xhs_cli, qrcode, playwright.sync_api\n"
        "from importlib.metadata import version\n"
        "from pip._vendor.packaging.version import Version\n"
        f"if Version(version('certifi')) < Version('{MIN_CERTIFI_VERSION}'): raise RuntimeError('certifi security update required')\n"
        "assert hasattr(click, 'command'), f'broken click module: {getattr(click, \"__file__\", \"<unknown>\")}'\n"
        "print('local dependencies ok')"
    )
    run([str(venv_python()), "-I", "-c", code])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-python", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    install_deps()
    if args.verify:
        verify_imports()
    if args.print_python:
        print(str(venv_python()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
