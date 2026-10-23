"""One explicitly confirmed action, at most one submission attempt.

Adapters return ``status/evidence/error/submitted``.  An adapter must set submitted
before attempting a write; a lost acknowledgement is never safe to retry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import unquote, urlparse

from json_utils import canonical_json


PLATFORMS = {"xhs", "douyin"}
ACTIONS = {"follow", "comment", "reply_comment", "send_dm"}
TERMINAL_STATUSES = {"succeeded", "failed", "unknown"}
_PROCESS_OWNER = f"{os.getpid()}:{secrets.token_hex(16)}"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("触达参数必须为对象。")
    platform = str(payload.get("platform") or "").strip().lower()
    action = str(payload.get("action") or "").strip()
    if platform not in PLATFORMS or action not in ACTIONS:
        raise ValueError("不支持的平台或触达动作。")
    data: dict[str, Any] = {"platform": platform, "action": action}
    for field in ("targetUserId", "noteId", "commentId"):
        value = str(payload.get(field) or "").strip()
        if field != "targetUserId" and value.startswith(f"{platform}:"):
            value = value[len(platform) + 1:]
        if value and not _ID_RE.fullmatch(value):
            raise ValueError(f"{field} 格式无效。")
        data[field] = value
    if action in {"follow", "send_dm", "reply_comment"} and not data["targetUserId"]:
        raise ValueError("缺少目标用户 ID。")
    if action in {"comment", "reply_comment"} and not data["noteId"]:
        raise ValueError("缺少目标笔记或作品 ID。")
    if action == "reply_comment" and not data["commentId"]:
        raise ValueError("缺少被回复的评论 ID。")
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise ValueError("正文必须为文本。")
    if action != "follow" and (not text.strip() or len(text) > 5000):
        raise ValueError("正文不能为空或超过 5000 字符。")
    if action == "follow" and text.strip():
        raise ValueError("关注动作不接受正文。")
    data["text"] = text
    data["leadId"] = str(payload.get("leadId") or "")[:300]
    target_url = str(payload.get("targetUrl") or "").strip()
    resource_id = data["targetUserId"] if action in {"follow", "send_dm"} else data["noteId"]
    paths = (f"/user/profile/{resource_id}",) if platform == "xhs" and action in {"follow", "send_dm"} else (
        (f"/explore/{resource_id}", f"/discovery/item/{resource_id}") if platform == "xhs" else
        ((f"/user/{resource_id}",) if action in {"follow", "send_dm"} else (f"/video/{resource_id}",))
    )
    host = "www.xiaohongshu.com" if platform == "xhs" else "www.douyin.com"
    if target_url:
        parsed = urlparse(target_url)
        if (parsed.scheme != "https" or parsed.hostname not in {host, host.removeprefix("www.")}
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or unquote(parsed.path).rstrip("/") not in paths):
            raise ValueError("目标链接必须属于对应平台，并与本次目标 ID 一致。")
    else:
        target_url = f"https://{host}{paths[0]}"
    data["targetUrl"] = target_url
    return data


def normalize_identity(account: Any, platform: str) -> dict[str, str]:
    if isinstance(account, str):
        identity, name = account.strip(), ""
    elif isinstance(account, dict):
        if account.get("authenticated") is False or account.get("ok") is False:
            raise ValueError("当前账号尚未通过身份核验。")
        identity = str(account.get("identity") or account.get("userId") or account.get("user_id") or "").strip()
        name = str(account.get("name") or account.get("nickname") or "")[:200]
    else:
        identity, name = "", ""
    if not identity or len(identity) > 200:
        raise ValueError("未取得稳定账号 ID，请先完成当前浏览器账号核验。")
    if ":" not in identity:
        identity = f"{platform}:{identity}"
    if not identity.startswith(f"{platform}:") or not _ID_RE.fullmatch(identity.split(":", 1)[1]):
        raise ValueError("账号 ID 格式或平台不一致。")
    return {"identity": identity, "name": name}


class OutreachService:
    def __init__(self, db_path: str | Path, account_provider: Callable[[str], Any],
                 adapter_provider: Callable[[str], Any]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.account_provider = account_provider
        self.adapter_provider = adapter_provider
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS outreach_actions (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    account_json TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    submitted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    execution_owner TEXT,
                    confirmed_at REAL,
                    execution_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS outreach_actions_created ON outreach_actions(created_at);
            """)
            # Another instance in this process may still own a live request.  A
            # crashed process's writes, however, must never return to prepared.
            for row in conn.execute("SELECT id, execution_owner FROM outreach_actions WHERE status = 'executing'"):
                owner = row["execution_owner"] or ""
                if owner == _PROCESS_OWNER:
                    continue
                try:
                    pid = int(owner.split(":", 1)[0])
                    if pid != os.getpid():
                        os.kill(pid, 0)
                        continue
                except (ValueError, OSError):
                    pass
                conn.execute("UPDATE outreach_actions SET status='unknown', submitted=1, error=?, updated_at=? WHERE id=?",
                             ("服务在执行期间中断，提交结果未知；请人工核对，禁止自动重发。", time.time(), row["id"]))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _binding(action_id: str, payload_json: str, account_json: str, expires_at: float, token: str) -> str:
        return hashlib.sha256(canonical_json([action_id, payload_json, account_json, float(expires_at), token]).encode()).hexdigest()

    @staticmethod
    def _fingerprint(payload: dict[str, Any], identity: str) -> str:
        # URLs/signatures and lead IDs are context, not a different recipient.
        if payload["action"] in {"follow", "send_dm"}:
            target = {"targetUserId": payload.get("targetUserId", "")}
        else:
            target = {"noteId": payload.get("noteId", "")}
            if payload["action"] == "reply_comment":
                target["commentId"] = payload.get("commentId", "")
        text = "" if payload["action"] == "follow" else payload.get("text", "")
        return canonical_json([identity, payload["platform"], payload["action"], target, text])

    def _assert_no_unresolved(self, conn: sqlite3.Connection, payload: dict[str, Any], identity: str,
                              exclude_id: str = "") -> None:
        fingerprint = self._fingerprint(payload, identity)
        for row in conn.execute("SELECT id, payload_json, account_json FROM outreach_actions WHERE status IN ('executing','unknown') AND id<>?", (exclude_id,)):
            existing = self._fingerprint(json.loads(row["payload_json"]), json.loads(row["account_json"])["identity"])
            if existing == fingerprint:
                raise ValueError(f"相同账号、目标和内容已有执行中或结果未知的动作（{row['id']}）；请先到平台人工核实，不能新建动作重发。")

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = normalize_payload(payload)
        account = normalize_identity(self.account_provider(data["platform"]), data["platform"])
        action_id, token, now = secrets.token_hex(16), secrets.token_urlsafe(32), time.time()
        expires_at = now + 15 * 60
        payload_json, account_json = canonical_json(data), canonical_json(account)
        token_hash = self._binding(action_id, payload_json, account_json, expires_at, token)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_no_unresolved(conn, data, account["identity"])
            conn.execute("""INSERT INTO outreach_actions
                (id, payload_json, account_json, token_hash, status, created_at, updated_at, expires_at, execution_count)
                VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?, 0)""",
                (action_id, payload_json, account_json, token_hash, now, now, expires_at))
        result = self.get(action_id)
        result["confirmationToken"] = token
        return result

    def get(self, action_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM outreach_actions WHERE id=?", (str(action_id),)).fetchone()
        if row is None:
            raise KeyError("触达动作不存在。")
        return self._public(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        count = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM outreach_actions ORDER BY created_at DESC LIMIT ?", (count,)).fetchall()
        return [self._public(row) for row in rows]

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        account = json.loads(row["account_json"])
        return {**json.loads(row["payload_json"]), "id": row["id"], "actionId": row["id"],
                "account": account, "accountIdentity": account["identity"], "status": row["status"],
                "evidence": json.loads(row["evidence_json"]), "error": row["error"],
                "submitted": bool(row["submitted"]), "createdAt": row["created_at"],
                "updatedAt": row["updated_at"], "expiresAt": row["expires_at"],
                "confirmedAt": row["confirmed_at"], "executionCount": row["execution_count"]}

    def execute(self, action_id: str, confirmation_token: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM outreach_actions WHERE id=?", (str(action_id),)).fetchone()
            if row is None:
                raise KeyError("触达动作不存在。")
            supplied = self._binding(row["id"], row["payload_json"], row["account_json"], row["expires_at"], str(confirmation_token or ""))
            if not hmac.compare_digest(row["token_hash"], supplied):
                raise ValueError("确认令牌无效；目标、正文或账号已变化，请重新预览。")
            if row["status"] != "prepared":
                return self._public(row)
            self._assert_no_unresolved(conn, json.loads(row["payload_json"]),
                                       json.loads(row["account_json"])["identity"], row["id"])
            if time.time() > row["expires_at"]:
                conn.execute("UPDATE outreach_actions SET status='failed', error=?, updated_at=? WHERE id=?",
                             ("确认已过期，请重新预览后确认。", time.time(), action_id))
                expired = True
            else:
                confirmed_at = time.time()
                conn.execute("""UPDATE outreach_actions SET status='executing', execution_owner=?, updated_at=?,
                    confirmed_at=?, execution_count=COALESCE(execution_count,0)+1 WHERE id=? AND status='prepared'""",
                             (_PROCESS_OWNER, confirmed_at, confirmed_at, action_id))
                expired = False
        if expired:
            return self.get(action_id)
        action = self.get(action_id)
        try:
            current = normalize_identity(self.account_provider(action["platform"]), action["platform"])
            if current["identity"] != action["accountIdentity"]:
                raise ValueError("当前登录账号与预览时不同；本次操作未提交，请重新预览。")
            adapter = self.adapter_provider(action["platform"])
        except Exception as exc:
            return self._finish(action_id, "failed", {}, f"账号或适配器核验失败：{exc}", False)
        try:
            raw = adapter.execute(action)
        except Exception:
            # An unstructured exception does not prove that no write occurred.
            return self._finish(action_id, "unknown", {}, "适配器异常退出，无法确认是否提交；请人工核对，禁止自动重发。", True)
        if not isinstance(raw, dict):
            return self._finish(action_id, "unknown", {}, "适配器未返回可核验结果，请人工核对。", True)
        submitted = raw.get("submitted") is not False
        status = str(raw.get("status") or "")
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
        error = str(raw.get("error") or "")[:2000]
        if status == "succeeded" and not self._verified(action, evidence):
            status, error = ("unknown" if submitted else "failed"), "没有与目标、正文及账号一致的成功证据，请人工核验。"
        elif status not in TERMINAL_STATUSES or (status == "failed" and submitted):
            status = "unknown" if submitted else "failed"
        if status == "unknown" and not error:
            error = "提交结果未知，请人工核对，禁止自动重发。"
        return self._finish(action_id, status, evidence, error, submitted)

    @staticmethod
    def _verified(action: dict[str, Any], evidence: dict[str, Any]) -> bool:
        if evidence.get("verified") is not True or not evidence.get("method") or not evidence.get("observedAt"):
            return False
        if evidence.get("accountIdentity") != action["accountIdentity"]:
            return False
        for field in ("targetUserId", "noteId", "commentId"):
            if action.get(field) and evidence.get(field) != action[field]:
                return False
        if action["action"] != "follow" and evidence.get("text") != action["text"]:
            return False
        return True

    def _finish(self, action_id: str, status: str, evidence: dict[str, Any], error: str, submitted: bool) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("""UPDATE outreach_actions SET status=?, evidence_json=?, error=?, submitted=?, updated_at=?
                WHERE id=? AND status='executing' AND execution_owner=?""",
                (status, canonical_json(evidence), error, int(submitted), time.time(), action_id, _PROCESS_OWNER))
        return self.get(action_id)
