#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import time


ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "open-leads-workbench"
IGNORED_NAMES = {
    ".env",
    ".runtime",
    "__pycache__",
    "config.local.json",
    "data",
    "exports",
    "logs",
}


def default_targets() -> list[Path]:
    home = Path.home()
    return [
        home / ".codex" / "skills" / SKILL_NAME,
        home / ".workbuddy" / "skills" / SKILL_NAME,
    ]


def ignore(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORED_NAMES or name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def ensure_safe_target(target: Path) -> None:
    resolved = target.resolve()
    home = Path.home().resolve()
    allowed = [
        home / ".codex" / "skills",
        home / ".workbuddy" / "skills",
    ]
    if not any(str(resolved).startswith(str(base.resolve()) + os.sep) for base in allowed):
        raise RuntimeError(f"Refusing to overwrite unexpected skill path: {resolved}")


def write_workbuddy_meta(target: Path) -> None:
    meta_path = target / "_user_meta.json"
    payload = {
        "name": SKILL_NAME,
        "installedAt": int(time.time() * 1000),
        "source": "localProject",
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_existing_target(target: Path) -> None:
    for child in target.iterdir():
        if child.name in IGNORED_NAMES:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def install(target: Path) -> None:
    ensure_safe_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        clean_existing_target(target)
        shutil.copytree(ROOT, target, ignore=ignore, dirs_exist_ok=True)
    else:
        shutil.copytree(ROOT, target, ignore=ignore)
    write_workbuddy_meta(target)
    print(f"Installed {SKILL_NAME}: {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", action="append", help="Optional explicit skill target directory.")
    args = parser.parse_args()
    targets = [Path(item).expanduser() for item in args.target] if args.target else default_targets()
    for target in targets:
        install(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
