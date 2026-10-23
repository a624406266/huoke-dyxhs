#!/usr/bin/env python3
"""Build a reviewed source snapshot, never a copy of the runtime directory.

Only local Git metadata is read. No dependency installation, network call,
authentication, commit, push, or deployment is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from typing import Any
import zipfile
from urllib.parse import unquote, urlsplit
import posixpath


BASELINE = "94c3db3"
ARCHIVE_ROOT = "keyword-workbench-v3"
APP = "xhs-ops-demo-skill"
ROOT_FILES = {"README.md", "requirements.txt", "start-linux.sh", ".gitignore", "LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE", "NOTICE.md"}
APP_FILES = {"README.md", "SKILL.md", "config.example.json", "requirements-local.txt", "start-macos.sh", "start-linux.sh", "start-windows.ps1"}
V3_DOCS = {"architecture.md", "user-guide.md", "acceptance.md", "certification.md", "release-notes.md", "test-report.md", "evolution.md"}
BLOCKED_PARTS = {".git", ".runtime", ".venv", "venv", "node_modules", "data", "logs", "exports", "dist", "build", "__pycache__", ".aws", ".codex", ".agents", ".workbuddy", "evidence", "credentials", "secrets", "chrome-profile"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def allowed_path(name: str, *, tracked: bool = False) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in name:
        return False
    lower_parts = {part.lower() for part in path.parts}
    if lower_parts & BLOCKED_PARTS:
        return False
    base = path.name.lower()
    if base.startswith(".env") or base.startswith("config.local") or any(word in base for word in ("cookie", "credential", "secret")):
        return False
    if name in ROOT_FILES:
        return True
    if path.parts[0] == "docs":
        if len(path.parts) == 3 and path.parts[1] == "keyword-workbench-v3":
            return path.name in V3_DOCS
        return tracked and len(path.parts) == 2 and path.suffix == ".md"
    if path.parts[0] != APP or len(path.parts) < 2:
        return False
    if len(path.parts) == 2:
        return path.name in APP_FILES
    section = path.parts[1]
    if section == "scripts":
        return path.suffix in {".py", ".sh", ".ps1"}
    if section == "tests":
        return path.suffix in {".py", ".js", ".cjs", ".html"}
    if section == "assets":
        return path.suffix in {".html", ".css", ".js", ".svg"}
    return tracked and section == "agents" and path.suffix in {".yaml", ".yml"}


def git_output(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("无法读取本地 Git 元数据；请在原仓库中打包。")
    return result.stdout


def git_paths(repo: Path, *args: str) -> set[str]:
    return {item.decode("utf-8") for item in git_output(repo, *args).split(b"\0") if item}


def validate_content(name: str, content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"源码白名单中出现非 UTF-8 文件：{name}") from exc
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ValueError(f"文件疑似含有密钥，停止打包且不输出命中内容：{name}")
    if name.endswith("/config.example.json"):
        def inspect(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    lowered = key.lower().replace("-", "_")
                    if lowered in {"api_key", "apikey", "password", "token", "secret", "cookie", "access_token"} and child:
                        raise ValueError("config.example.json 的敏感配置字段必须为空。")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        inspect(json.loads(text))


def validate_document_links(files: dict[str, bytes]) -> None:
    """Every relative Markdown link must resolve within the source archive."""
    for name, content in files.items():
        if not name.endswith(".md"):
            continue
        text = re.sub(r"```[\s\S]*?```", "", content.decode("utf-8"))
        for match in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            target = match.group(1).strip()
            if not target:
                continue
            target = target[1:target.index(">")] if target.startswith("<") and ">" in target else target.split()[0]
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path)))
            if resolved not in files:
                raise ValueError(f"交付文档链接目标缺失：{name} → {target}")


def collect_files(repo: Path, tracked: set[str], untracked: set[str]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name in sorted(tracked | untracked):
        if not allowed_path(name, tracked=name in tracked):
            continue
        path = repo / name
        if not path.exists() or not path.is_file():
            continue
        # Never follow either a file or an ancestor symlink into local secrets.
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != repo and repo in parent.parents):
            continue
        try:
            path.resolve().relative_to(repo.resolve())
        except ValueError:
            continue
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError(f"源码在读取期间变化，请在编辑完成后重新打包：{name}")
        validate_content(name, data)
        files[name] = data
    if "README.md" not in files or f"{APP}/config.example.json" not in files:
        raise ValueError("交付源缺少 README.md 或 config.example.json。")
    validate_document_links(files)
    return files


def build_archive(repo: Path, output_dir: Path, name: str, files: dict[str, bytes], metadata: dict[str, Any]) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", name):
        raise ValueError("包名只能含字母、数字、点、下划线和连字符。")
    validate_document_links(files)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{name}.zip"
    manifest_path = output_dir / f"{name}.manifest.json"
    hash_path = output_dir / f"{name}.sha256"
    if any(path.exists() for path in (archive, manifest_path, hash_path)):
        raise FileExistsError("目标交付文件已存在，请使用新包名；不会覆盖。")
    manifest = {
        "formatVersion": 1, "project": ARCHIVE_ROOT, "sourceBaseline": BASELINE,
        "packagedAt": datetime.now(timezone.utc).isoformat(), **metadata,
        "scope": "Source snapshot only; platform login, credentials, runtime data and dependencies are excluded.",
        "acceptanceStatus": "Pending real-user platform, dataset, four-week usage and Windows acceptance.",
        "files": [{"path": path, "size": len(data), "sha256": sha256(data)} for path, data in sorted(files.items())],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, data in [*sorted(files.items()), ("release-manifest.json", manifest_bytes)]:
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | (0o755 if path.endswith(".sh") else 0o644)) << 16
            bundle.writestr(info, data)
    verification = verify_archive(archive)
    with manifest_path.open("xb") as stream:
        stream.write(manifest_bytes)
    digest = sha256(archive.read_bytes())
    with hash_path.open("x", encoding="utf-8") as stream:
        stream.write(f"{digest}  {archive.name}\n")
    return {"archive": str(archive), "manifest": str(manifest_path), "hashFile": str(hash_path),
            "sha256": digest, "files": verification["files"], "bytes": archive.stat().st_size}


def verify_archive(archive: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("压缩包包含重复文件名。")
        manifest_name = f"{ARCHIVE_ROOT}/release-manifest.json"
        manifest = json.loads(bundle.read(manifest_name))
        entries = manifest.get("files") or []
        expected = {manifest_name}
        files = {}
        for entry in entries:
            path = entry["path"]
            if not allowed_path(path, tracked=True):
                raise ValueError("压缩包清单含白名单外路径。")
            member = f"{ARCHIVE_ROOT}/{path}"
            if member in expected:
                raise ValueError("清单包含重复路径。")
            expected.add(member)
            info = bundle.getinfo(member)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("压缩包不允许符号链接。")
            data = bundle.read(member)
            if len(data) != entry["size"] or sha256(data) != entry["sha256"]:
                raise ValueError(f"文件与交付清单 hash 不一致：{path}")
            validate_content(path, data)
            files[path] = data
        if expected != set(names):
            raise ValueError("压缩包存在清单外内容。")
        validate_document_links(files)
    return {"ok": True, "files": len(entries), "sha256": sha256(archive.read_bytes())}


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="keyword-release-test-") as temporary:
        repo = Path(temporary)
        fixture = {
            "README.md": "# Fixture\n",
            f"{APP}/config.example.json": '{"llm":{"api_key":""}}\n',
            f"{APP}/scripts/example.py": "print('fixture')\n",
            f"{APP}/tests/test_example.py": "# synthetic unit test\n",
            f"{APP}/tests/test_frontend.cjs": "// synthetic browser test\n",
            "docs/keyword-workbench-v3/acceptance.md": "# Pending acceptance\n",
            "docs/keyword-workbench-v3/test-report.md": "# Local test report\n",
            f"{APP}/config.local.json": '{"private":"do not include"}',
            f"{APP}/data/source.py": "# never include runtime data",
            f"{APP}/.runtime/source.py": "# never include runtime environment",
            f"{APP}/scripts/__pycache__/cached.py": "# never include cache",
            f"{APP}/scripts/credentials.py": "# never include credential file",
            "docs/evidence/actual-task.md": "private evidence",
            ".git/config": "private Git configuration",
        }
        for name, content in fixture.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        tracked = set(fixture) - {f"{APP}/scripts/example.py", "docs/keyword-workbench-v3/acceptance.md"}
        files = collect_files(repo, tracked, set(fixture) - tracked)
        expected = {"README.md", f"{APP}/config.example.json", f"{APP}/scripts/example.py", f"{APP}/tests/test_example.py", f"{APP}/tests/test_frontend.cjs", "docs/keyword-workbench-v3/acceptance.md", "docs/keyword-workbench-v3/test-report.md"}
        assert set(files) == expected
        result = build_archive(repo, repo / "dist", "fixture", files, {"head": "fixture", "workingTreeChanges": True})
        assert verify_archive(Path(result["archive"]))["files"] == len(expected)
        try:
            validate_content(f"{APP}/config.example.json", b'{"api_key":"nonempty-fixture"}')
        except ValueError:
            pass
        else:
            raise AssertionError("nonempty credentials were accepted")
        try:
            validate_content("README.md", ("s" + "k-" + "a" * 40).encode())
        except ValueError:
            pass
        else:
            raise AssertionError("key-shaped content was accepted")
        with zipfile.ZipFile(result["archive"], "a") as bundle:
            bundle.writestr(f"{ARCHIVE_ROOT}/{APP}/config.local.json", "private")
        try:
            verify_archive(Path(result["archive"]))
        except ValueError:
            pass
        else:
            raise AssertionError("unlisted runtime file was accepted")
    return {"ok": True, "checks": ["tracked-and-new-source", "runtime-and-secret-path-exclusion", "archive-and-file-hashes", "nonempty-config-rejection", "key-shaped-content-rejection", "unlisted-entry-rejection"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify", type=Path, help="Verify an existing source ZIP against its embedded manifest")
    parser.add_argument("--name", help="Safe output basename; existing files are never overwritten")
    args = parser.parse_args()
    if args.self_test:
        result = self_test()
    elif args.verify:
        result = verify_archive(args.verify)
    else:
        repo = Path(__file__).resolve().parents[2]
        tracked = git_paths(repo, "ls-files", "-z")
        untracked = git_paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
        files = collect_files(repo, tracked, untracked)
        changed = git_paths(repo, "diff", "--name-only", "HEAD", "-z")
        head = git_output(repo, "rev-parse", "HEAD").decode().strip()
        metadata = {"head": head, "workingTreeChanges": bool((changed | untracked) & set(files)),
                    "modifiedOrNewIncludedFiles": sorted((changed | untracked) & set(files)),
                    "sourceOwnership": "Existing user repository; baseline authorship remains in the original Git history."}
        name = args.name or "keyword-workbench-v3-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        result = build_archive(repo, repo / "dist", name, files, metadata)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
