from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator


PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260000
LEGACY_SALES_USER_ID = 0


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password 不能为空")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = str(password_hash or "").split("$", 3)
        if algorithm != PASSWORD_HASH_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations_text),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def is_password_hash(value: str) -> bool:
    return str(value or "").startswith(f"{PASSWORD_HASH_ALGORITHM}$")


class SalesStorage:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path("sales_leads.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    display_name TEXT,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL,
                    updated_at REAL,
                    disabled_at REAL,
                    password_reset_at REAL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    created_at REAL,
                    expires_at REAL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    mode TEXT,
                    business_direction TEXT,
                    demand_description TEXT,
                    keyword TEXT,
                    payload TEXT,
                    status_text TEXT,
                    progress_json TEXT,
                    result_json TEXT,
                    started_at REAL,
                    finished_at REAL,
                    updated_at REAL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    agent TEXT,
                    message TEXT NOT NULL,
                    data TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS audit_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    stage TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    note_id TEXT,
                    source_keyword TEXT,
                    decision TEXT,
                    reason TEXT,
                    evidence_json TEXT,
                    score REAL,
                    confidence REAL,
                    priority TEXT,
                    risk TEXT,
                    payload_json TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    note_id TEXT NOT NULL,
                    task_id INTEGER,
                    title TEXT,
                    author TEXT,
                    url TEXT,
                    payload TEXT,
                    created_at REAL,
                    UNIQUE(sales_user_id, note_id)
                );
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    comment_id TEXT NOT NULL,
                    task_id INTEGER,
                    note_id TEXT,
                    nickname TEXT,
                    user_id TEXT,
                    content TEXT,
                    payload TEXT,
                    created_at REAL,
                    UNIQUE(sales_user_id, comment_id)
                );
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    lead_id TEXT NOT NULL,
                    task_id INTEGER,
                    note_id TEXT,
                    comment_id TEXT,
                    score INTEGER,
                    priority TEXT,
                    product_tags TEXT,
                    demand_summary TEXT,
                    evidence TEXT,
                    outreach_copy TEXT,
                    feedback TEXT,
                    payload TEXT,
                    created_at REAL,
                    UNIQUE(sales_user_id, lead_id)
                );
                CREATE TABLE IF NOT EXISTS lead_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    task_id INTEGER,
                    lead_id TEXT,
                    comment_id TEXT,
                    feedback_type TEXT NOT NULL,
                    feedback TEXT,
                    user_note TEXT,
                    evidence TEXT,
                    expected_result TEXT,
                    status TEXT NOT NULL,
                    analysis_json TEXT,
                    candidate_prompt_id INTEGER,
                    target_agent_key TEXT,
                    created_at REAL,
                    updated_at REAL,
                    applied_at REAL,
                    dismissed_at REAL
                );
                CREATE TABLE IF NOT EXISTS prompt_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    prompt_version TEXT,
                    model TEXT,
                    input_summary TEXT,
                    output_summary TEXT,
                    error TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    agent_key TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    prompt_version TEXT,
                    model TEXT,
                    purpose TEXT,
                    input_summary TEXT,
                    output_summary TEXT,
                    confidence REAL,
                    error TEXT,
                    duration_ms INTEGER,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS prompt_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_key TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL,
                    prompt TEXT NOT NULL,
                    notes TEXT,
                    source_task_id INTEGER,
                    metrics TEXT,
                    created_at REAL,
                    activated_at REAL,
                    disabled_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_agent_version
                ON prompt_versions(agent_key, version);
                CREATE TABLE IF NOT EXISTS eval_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    case_type TEXT,
                    input_excerpt TEXT,
                    expected_behavior TEXT,
                    source_id TEXT,
                    payload TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    eval_type TEXT,
                    agent_key TEXT,
                    score REAL,
                    payload TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS task_summaries (
                    task_id INTEGER PRIMARY KEY,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    summary TEXT,
                    agent_summaries TEXT,
                    evolution_summary TEXT,
                    payload TEXT,
                    created_at REAL
                );
                """
            )
            self._migrate_common(conn)
            self._migrate_sqlite_tenant_tables(conn)
            self._create_indexes(conn)

    def _migrate_common(self, conn: Any) -> None:
        real_type = "REAL"
        self._add_columns(
            conn,
            "tasks",
            {
                "sales_user_id": "INTEGER NOT NULL DEFAULT 0",
                "mode": "TEXT",
                "demand_description": "TEXT",
                "payload": "TEXT",
                "status_text": "TEXT",
                "progress_json": "TEXT",
                "result_json": "TEXT",
                "updated_at": real_type,
            },
        )
        for table in ("notes", "comments", "leads", "lead_feedback", "task_summaries"):
            self._add_columns(conn, table, {"sales_user_id": "INTEGER NOT NULL DEFAULT 0"})
        self._add_columns(conn, "audit_decisions", {"platform": "TEXT NOT NULL DEFAULT 'xhs'"})
        for table in ("notes", "comments", "leads"):
            self._add_columns(conn, table, {"platform": "TEXT NOT NULL DEFAULT 'xhs'"})
            conn.execute(f"UPDATE {table} SET platform = 'xhs' WHERE platform IS NULL OR platform = ''")
        conn.execute("UPDATE audit_decisions SET platform = 'xhs' WHERE platform IS NULL OR platform = ''")
        self._backfill_sales_user_ids(conn)

    def _add_columns(self, conn: Any, table: str, additions: dict[str, str]) -> None:
        columns = self._columns(conn, table)
        for column, column_type in additions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _columns(self, conn: Any, table: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}

    def _create_indexes(self, conn: Any) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_sales_user ON tasks(sales_user_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_decisions_task ON audit_decisions(task_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_decisions_sales ON audit_decisions(sales_user_id, task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_decisions_platform ON audit_decisions(task_id, platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_decisions_stage ON audit_decisions(task_id, stage, entity_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_decisions_keyword ON audit_decisions(task_id, source_keyword)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_task_id ON notes(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_sales_user ON notes(sales_user_id, task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_platform ON notes(sales_user_id, platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_sales_user ON comments(sales_user_id, task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_platform ON comments(sales_user_id, platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_task_id ON leads(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_sales_user ON leads(sales_user_id, task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_platform ON leads(sales_user_id, platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_feedback_status ON lead_feedback(status, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_feedback_task_id ON lead_feedback(task_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_feedback_sales_user ON lead_feedback(sales_user_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_task_id ON agent_runs(task_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_versions_agent_status ON prompt_versions(agent_key, status)")

    def _backfill_sales_user_ids(self, conn: Any) -> None:
        for table in ("notes", "comments", "leads", "lead_feedback", "task_summaries"):
            if "sales_user_id" not in self._columns(conn, table):
                continue
            conn.execute(
                f"""
                UPDATE {table}
                SET sales_user_id = COALESCE(
                    (SELECT sales_user_id FROM tasks WHERE tasks.id = {table}.task_id),
                    sales_user_id,
                    0
                )
                WHERE COALESCE(sales_user_id, 0) = 0 AND task_id IS NOT NULL
                """
            )

    def _migrate_sqlite_tenant_tables(self, conn: Any) -> None:
        for table in ("notes", "comments", "leads"):
            columns = self._sqlite_column_info(conn, table)
            if "id" not in columns or int(columns.get("id", {}).get("pk") or 0) != 1:
                self._rebuild_sqlite_tenant_table(conn, table)

    def _sqlite_column_info(self, conn: Any, table: str) -> dict[str, dict[str, Any]]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]): dict(row) for row in rows}

    def _rebuild_sqlite_tenant_table(self, conn: Any, table: str) -> None:
        legacy = f"{table}_legacy_{int(time.time() * 1000)}"
        conn.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
        self._create_sqlite_tenant_table(conn, table)
        rows = conn.execute(f"SELECT * FROM {legacy}").fetchall()
        task_owner_cache: dict[int, int] = {}
        for row in rows:
            data = {key: row[key] for key in row.keys()}
            task_id = data.get("task_id")
            sales_user_id = int(data.get("sales_user_id") or self._task_owner(conn, task_id, task_owner_cache) or 0)
            if table == "notes":
                self._insert_migrated_note(conn, sales_user_id, data)
            elif table == "comments":
                self._insert_migrated_comment(conn, sales_user_id, data)
            elif table == "leads":
                self._insert_migrated_lead(conn, sales_user_id, data)
        conn.execute(f"DROP TABLE {legacy}")

    def _create_sqlite_tenant_table(self, conn: Any, table: str) -> None:
        schemas = {
            "notes": """
                CREATE TABLE notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    note_id TEXT NOT NULL,
                    task_id INTEGER,
                    title TEXT,
                    author TEXT,
                    url TEXT,
                    payload TEXT,
                    created_at REAL,
                    UNIQUE(sales_user_id, note_id)
                )
            """,
            "comments": """
                CREATE TABLE comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    comment_id TEXT NOT NULL,
                    task_id INTEGER,
                    note_id TEXT,
                    nickname TEXT,
                    user_id TEXT,
                    content TEXT,
                    payload TEXT,
                    created_at REAL,
                    UNIQUE(sales_user_id, comment_id)
                )
            """,
            "leads": """
                CREATE TABLE leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_user_id INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL DEFAULT 'xhs',
                    lead_id TEXT NOT NULL,
                    task_id INTEGER,
                    note_id TEXT,
                    comment_id TEXT,
                    score INTEGER,
                    priority TEXT,
                    product_tags TEXT,
                    demand_summary TEXT,
                    evidence TEXT,
                    outreach_copy TEXT,
                    feedback TEXT,
                    payload TEXT,
                    created_at REAL,
                    UNIQUE(sales_user_id, lead_id)
                )
            """,
        }
        conn.execute(schemas[table])

    def _task_owner(self, conn: Any, task_id: Any, cache: dict[int, int] | None = None) -> int:
        try:
            normalized = int(task_id)
        except Exception:
            return LEGACY_SALES_USER_ID
        cache = cache if cache is not None else {}
        if normalized in cache:
            return cache[normalized]
        row = conn.execute("SELECT sales_user_id FROM tasks WHERE id = ?", (normalized,)).fetchone()
        cache[normalized] = int((row["sales_user_id"] if row else None) or LEGACY_SALES_USER_ID)
        return cache[normalized]

    def _insert_migrated_note(self, conn: Any, sales_user_id: int, data: dict[str, Any]) -> None:
        note_id = str(data.get("note_id") or "")
        if not note_id:
            return
        platform = str(data.get("platform") or "xhs")
        conn.execute(
            """
            INSERT INTO notes(sales_user_id, platform, note_id, task_id, title, author, url, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sales_user_id, note_id) DO UPDATE SET
                platform = excluded.platform,
                task_id = excluded.task_id,
                title = excluded.title,
                author = excluded.author,
                url = excluded.url,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                sales_user_id,
                platform,
                note_id,
                data.get("task_id"),
                data.get("title"),
                data.get("author"),
                data.get("url"),
                data.get("payload"),
                data.get("created_at"),
            ),
        )

    def ensure_admin_user(self, username: str, password_hash: str, display_name: str = "管理员") -> dict[str, Any]:
        if not is_password_hash(password_hash):
            raise ValueError("管理员密码必须使用哈希配置。")
        return self.upsert_user(
            username=username,
            role="admin",
            password_hash=password_hash,
            display_name=display_name or username,
            enabled=True,
        )

    def has_admin_user(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE role = 'admin' AND enabled = 1 LIMIT 1").fetchone()
        return bool(row)

    def upsert_sales_user(
        self,
        *,
        username: str,
        password_hash: str | None = None,
        display_name: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        if password_hash is not None and not is_password_hash(password_hash):
            raise ValueError("销售密码必须先转换为哈希。")
        return self.upsert_user(
            username=username,
            role="sales",
            password_hash=password_hash,
            display_name=display_name or username,
            enabled=enabled,
        )

    def upsert_user(
        self,
        *,
        username: str,
        role: str,
        password_hash: str | None = None,
        display_name: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        username = str(username or "").strip().lower()
        role = str(role or "").strip().lower()
        if not username:
            raise ValueError("username 不能为空")
        if role not in {"admin", "sales"}:
            raise ValueError("role 不合法")
        now = time.time()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                if password_hash:
                    conn.execute(
                        """
                        UPDATE users
                        SET role = ?, display_name = ?, password_hash = ?, enabled = ?, updated_at = ?,
                            disabled_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(disabled_at, ?) END,
                            password_reset_at = ?
                        WHERE username = ?
                        """,
                        (
                            role,
                            display_name,
                            password_hash,
                            1 if enabled else 0,
                            now,
                            1 if enabled else 0,
                            now,
                            now,
                            username,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE users
                        SET role = ?, display_name = ?, enabled = ?, updated_at = ?,
                            disabled_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(disabled_at, ?) END
                        WHERE username = ?
                        """,
                        (role, display_name, 1 if enabled else 0, now, 1 if enabled else 0, now, username),
                    )
            else:
                if not password_hash:
                    raise ValueError("新用户必须设置密码哈希。")
                conn.execute(
                    """
                    INSERT INTO users(username, role, display_name, password_hash, enabled, created_at, updated_at, disabled_at, password_reset_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (username, role, display_name, password_hash, 1 if enabled else 0, now, now, None if enabled else now, now),
                )
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._user_to_dict(user)

    def list_sales_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM users
                WHERE role = 'sales'
                ORDER BY enabled DESC, username ASC
                """
            ).fetchall()
        return [self._user_to_dict(row) for row in rows]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._user_to_dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (str(username or "").strip().lower(),)).fetchone()
        return self._user_to_dict(row) if row else None

    def authenticate_user(self, username: str, password: str, role: str | None = None) -> dict[str, Any] | None:
        normalized = str(username or "").strip().lower()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (normalized,)).fetchone()
        if not row:
            return None
        if role and row["role"] != role:
            return None
        if not bool(row["enabled"]):
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return self._user_to_dict(row)

    def set_user_enabled(self, user_id: int, enabled: bool) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET enabled = ?, updated_at = ?, disabled_at = ?
                WHERE id = ? AND role = 'sales'
                """,
                (1 if enabled else 0, now, None if enabled else now, int(user_id)),
            )
            if not enabled:
                conn.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                    (now, int(user_id)),
                )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            raise ValueError("销售用户不存在")
        return self._user_to_dict(row)

    def reset_user_password(self, user_id: int, password_hash: str) -> dict[str, Any]:
        if not is_password_hash(password_hash):
            raise ValueError("密码必须先转换为哈希。")
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, updated_at = ?, password_reset_at = ?
                WHERE id = ? AND role = 'sales'
                """,
                (password_hash, now, now, int(user_id)),
            )
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, int(user_id)),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            raise ValueError("销售用户不存在")
        return self._user_to_dict(row)

    def create_session(self, user_id: int, ttl_seconds: int = 86400) -> dict[str, Any]:
        user = self.get_user(user_id)
        if not user or not user.get("enabled"):
            raise ValueError("用户不存在或已禁用")
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = time.time()
        expires_at = now + max(60, int(ttl_seconds))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(token_hash, user_id, role, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (token_hash, int(user_id), user["role"], now, expires_at),
            )
        return {"token": token, "expiresAt": expires_at, "user": user}

    def get_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        token_hash = self._hash_token(token)
        now = time.time()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.id AS session_id, s.token_hash, s.created_at AS session_created_at,
                       s.expires_at, s.revoked_at, u.*
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
        if not row or not bool(row["enabled"]):
            return None
        user = self._user_to_dict(row)
        return {
            "sessionId": int(row["session_id"]),
            "tokenHash": row["token_hash"],
            "createdAt": row["session_created_at"],
            "expiresAt": row["expires_at"],
            "user": user,
            "role": user["role"],
        }

    def revoke_session(self, token: str) -> None:
        if not token:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (time.time(), self._hash_token(token)),
            )

    def delete_expired_sessions(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL", (time.time(),))

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    def _user_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "username": row["username"],
            "role": row["role"],
            "displayName": row["display_name"] or row["username"],
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "disabledAt": row["disabled_at"],
            "passwordResetAt": row["password_reset_at"],
        }

    def _insert_migrated_comment(self, conn: Any, sales_user_id: int, data: dict[str, Any]) -> None:
        comment_id = str(data.get("comment_id") or "")
        if not comment_id:
            return
        platform = str(data.get("platform") or "xhs")
        conn.execute(
            """
            INSERT INTO comments(sales_user_id, platform, comment_id, task_id, note_id, nickname, user_id, content, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sales_user_id, comment_id) DO UPDATE SET
                platform = excluded.platform,
                task_id = excluded.task_id,
                note_id = excluded.note_id,
                nickname = excluded.nickname,
                user_id = excluded.user_id,
                content = excluded.content,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                sales_user_id,
                platform,
                comment_id,
                data.get("task_id"),
                data.get("note_id"),
                data.get("nickname"),
                data.get("user_id"),
                data.get("content"),
                data.get("payload"),
                data.get("created_at"),
            ),
        )

    def _insert_migrated_lead(self, conn: Any, sales_user_id: int, data: dict[str, Any]) -> None:
        lead_id = str(data.get("lead_id") or "")
        if not lead_id:
            return
        platform = str(data.get("platform") or "xhs")
        conn.execute(
            """
            INSERT INTO leads(sales_user_id, platform, lead_id, task_id, note_id, comment_id, score, priority,
            product_tags, demand_summary, evidence, outreach_copy, feedback, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sales_user_id, lead_id) DO UPDATE SET
                platform = excluded.platform,
                task_id = excluded.task_id,
                note_id = excluded.note_id,
                comment_id = excluded.comment_id,
                score = excluded.score,
                priority = excluded.priority,
                product_tags = excluded.product_tags,
                demand_summary = excluded.demand_summary,
                evidence = excluded.evidence,
                outreach_copy = excluded.outreach_copy,
                feedback = excluded.feedback,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                sales_user_id,
                platform,
                lead_id,
                data.get("task_id"),
                data.get("note_id"),
                data.get("comment_id"),
                data.get("score"),
                data.get("priority"),
                data.get("product_tags"),
                data.get("demand_summary"),
                data.get("evidence"),
                data.get("outreach_copy"),
                data.get("feedback"),
                data.get("payload"),
                data.get("created_at"),
            ),
        )

    def create_task(
        self,
        payload: dict[str, Any],
        mode: str = "single",
        status_text: str = "任务已创建",
        sales_user_id: int | None = None,
    ) -> int:
        now = time.time()
        owner_id = int(sales_user_id or LEGACY_SALES_USER_ID)
        demand_description = str(payload.get("demandDescription") or payload.get("demand_description") or "").strip()
        if not demand_description:
            demand_description = str(payload.get("businessDirectionLabel") or payload.get("businessDirection") or "").strip()
        with self.connect() as conn:
            values = (
                owner_id,
                "running",
                mode,
                payload.get("businessDirection"),
                demand_description,
                payload.get("keyword"),
                json.dumps(payload, ensure_ascii=False),
                status_text,
                json.dumps({"percent": 0}, ensure_ascii=False),
                now,
                now,
            )
            sql = """
                INSERT INTO tasks(sales_user_id, status, mode, business_direction, demand_description, keyword, payload, status_text,
                progress_json, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cur = conn.execute(sql, values)
            return int(cur.lastrowid)

    def finish_task(self, task_id: int, status: str = "completed", error: str = "", result: dict[str, Any] | None = None, status_text: str | None = None) -> None:
        now = time.time()
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        with self.connect() as conn:
            if result_json is None:
                conn.execute(
                    "UPDATE tasks SET status = ?, finished_at = ?, updated_at = ?, error = ?, status_text = COALESCE(?, status_text) WHERE id = ?",
                    (status, now, now, error, status_text, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE tasks SET status = ?, finished_at = ?, updated_at = ?, error = ?,
                    status_text = COALESCE(?, status_text), result_json = ? WHERE id = ?
                    """,
                    (status, now, now, error, status_text, result_json, task_id),
                )

    def update_task(
        self,
        task_id: int,
        *,
        status: str | None = None,
        status_text: str | None = None,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if status_text is not None:
            assignments.append("status_text = ?")
            values.append(status_text)
        if progress is not None:
            assignments.append("progress_json = ?")
            values.append(json.dumps(progress, ensure_ascii=False))
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            assignments.append("error = ?")
            values.append(error)
        values.append(task_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", values)

    def add_task_event(self, task_id: int, level: str, agent: str, message: str, data: Any | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO task_events(task_id, level, agent, message, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    level,
                    agent,
                    message,
                    json.dumps(data, ensure_ascii=False) if data is not None else "",
                    time.time(),
                ),
            )

    def mark_stale_running_as_interrupted(self) -> None:
        now = time.time()
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM tasks WHERE status = 'running'").fetchall()
            for row in rows:
                task_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE tasks SET status = ?, finished_at = ?, updated_at = ?, error = ?,
                    status_text = ? WHERE id = ?
                    """,
                    ("interrupted", now, now, "后端已重启，原运行线程已不存在。", "后端已重启，任务已中断。", task_id),
                )
                conn.execute(
                    """
                    INSERT INTO task_events(task_id, level, agent, message, data, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, "warning", "Task Manager", "后端重启，运行中任务已标记为 interrupted。", "", now),
                )

    def list_tasks(self, limit: int = 20, sales_user_id: int | None = None) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if sales_user_id is not None:
            where = "WHERE t.sales_user_id = ?"
            params.append(int(sales_user_id))
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*,
                       (SELECT COUNT(*) FROM notes n WHERE n.task_id = t.id) AS notes_count,
                       (SELECT COUNT(*) FROM leads l WHERE l.task_id = t.id) AS leads_count,
                       (SELECT COUNT(*) FROM task_events e WHERE e.task_id = t.id) AS events_count
                FROM tasks t
                {where}
                ORDER BY COALESCE(t.updated_at, t.started_at) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._task_summary(row) for row in rows]

    def get_task(self, task_id: int, event_limit: int = 50, sales_user_id: int | None = None) -> dict[str, Any] | None:
        where = "t.id = ?"
        params: list[Any] = [task_id]
        if sales_user_id is not None:
            where += " AND t.sales_user_id = ?"
            params.append(int(sales_user_id))
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT t.*,
                       (SELECT COUNT(*) FROM notes n WHERE n.task_id = t.id) AS notes_count,
                       (SELECT COUNT(*) FROM leads l WHERE l.task_id = t.id) AS leads_count,
                       (SELECT COUNT(*) FROM task_events e WHERE e.task_id = t.id) AS events_count
                FROM tasks t WHERE {where}
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT id, level, agent, message, data, created_at
                    FROM task_events
                    WHERE task_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (task_id, event_limit),
            ).fetchall()
        task = self._task_summary(row)
        task["events"] = [self._event_to_dict(event) for event in event_rows]
        task["result"] = self._json_loads(row["result_json"], None)
        return task

    def _task_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "salesUserId": int(row["sales_user_id"] or LEGACY_SALES_USER_ID),
            "status": row["status"],
            "mode": row["mode"] or "single",
            "businessDirection": row["business_direction"] or "",
            "demandDescription": row["demand_description"] or (self._json_loads(row["payload"], {}) or {}).get("demandDescription", ""),
            "keyword": row["keyword"] or "",
            "payload": self._json_loads(row["payload"], {}),
            "statusText": row["status_text"] or "",
            "progress": self._json_loads(row["progress_json"], {}),
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "updatedAt": row["updated_at"] or row["started_at"],
            "error": row["error"] or "",
            "notesCount": int(row["notes_count"] or 0),
            "leadsCount": int(row["leads_count"] or 0),
            "eventsCount": int(row["events_count"] or 0),
        }

    def save_audit_decisions(self, task_id: int | None, items: list[dict[str, Any]]) -> None:
        if not task_id or not items:
            return
        now = time.time()
        with self.connect() as conn:
            sales_user_id = self._task_owner(conn, task_id)
            rows = []
            for item in items:
                rows.append(
                    (
                        int(task_id),
                        sales_user_id,
                        str(item.get("platform") or "xhs"),
                        str(item.get("stage") or ""),
                        str(item.get("entity_type") or item.get("entityType") or ""),
                        str(item.get("entity_id") or item.get("entityId") or ""),
                        str(item.get("note_id") or item.get("noteId") or ""),
                        str(item.get("source_keyword") or item.get("sourceKeyword") or ""),
                        str(item.get("decision") or ""),
                        str(item.get("reason") or "")[:1000],
                        self._compact_json(item.get("evidence") or item.get("evidence_json") or item.get("evidenceJson") or []),
                        self._optional_float(item.get("score")),
                        self._optional_float(item.get("confidence")),
                        str(item.get("priority") or ""),
                        str(item.get("risk") or "")[:1000],
                        self._compact_json(item.get("payload") or {}),
                        now,
                    )
                )
            conn.executemany(
                """
                INSERT INTO audit_decisions(task_id, sales_user_id, platform, stage, entity_type, entity_id, note_id,
                source_keyword, decision, reason, evidence_json, score, confidence, priority, risk, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_audit_decision(self, task_id: int | None, **item: Any) -> None:
        self.save_audit_decisions(task_id, [item])

    def list_audit_decisions(
        self,
        task_id: int,
        *,
        stage: str = "",
        entity_type: str = "",
        decision: str = "",
        keyword: str = "",
        limit: int = 100,
        offset: int = 0,
        sales_user_id: int | None = None,
    ) -> dict[str, Any]:
        filters = ["task_id = ?"]
        params: list[Any] = [int(task_id)]
        if sales_user_id is not None:
            filters.append("sales_user_id = ?")
            params.append(int(sales_user_id))
        if stage:
            filters.append("stage = ?")
            params.append(stage)
        if entity_type:
            filters.append("entity_type = ?")
            params.append(entity_type)
        if decision:
            filters.append("decision = ?")
            params.append(decision)
        if keyword:
            filters.append("source_keyword LIKE ?")
            params.append(f"%{keyword}%")
        where = " AND ".join(filters)
        limit = min(max(int(limit), 1), 200)
        offset = max(int(offset), 0)
        with self.connect() as conn:
            total_row = conn.execute(f"SELECT COUNT(*) AS total FROM audit_decisions WHERE {where}", params).fetchone()
            rows = conn.execute(
                f"""
                SELECT * FROM audit_decisions
                WHERE {where}
                ORDER BY id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [self._audit_decision_to_dict(row) for row in rows],
            "total": int(total_row["total"] or 0) if total_row else 0,
            "limit": limit,
            "offset": offset,
        }

    def _event_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "level": row["level"],
            "agent": row["agent"] or "",
            "message": row["message"],
            "data": self._json_loads(row["data"], None),
            "createdAt": row["created_at"],
        }

    def _audit_decision_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "taskId": int(row["task_id"]),
            "salesUserId": int(row["sales_user_id"] or LEGACY_SALES_USER_ID),
            "platform": row["platform"] or "xhs",
            "stage": row["stage"] or "",
            "entityType": row["entity_type"] or "",
            "entityId": row["entity_id"] or "",
            "noteId": row["note_id"] or "",
            "sourceKeyword": row["source_keyword"] or "",
            "decision": row["decision"] or "",
            "reason": row["reason"] or "",
            "evidence": self._json_loads(row["evidence_json"], []),
            "score": row["score"],
            "confidence": row["confidence"],
            "priority": row["priority"] or "",
            "risk": row["risk"] or "",
            "payload": self._json_loads(row["payload_json"], {}),
            "createdAt": row["created_at"],
        }

    def _json_loads(self, value: Any, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    def _compact_json(self, value: Any, limit: int = 4000) -> str:
        text = json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)
        return text[:limit]

    def _optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def save_note(self, task_id: int, note: dict[str, Any]) -> None:
        platform = str(note.get("platform") or "xhs")
        with self.connect() as conn:
            sales_user_id = self._task_owner(conn, task_id)
            conn.execute(
                """
                INSERT INTO notes(sales_user_id, platform, note_id, task_id, title, author, url, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sales_user_id, note_id) DO UPDATE SET
                    platform = excluded.platform,
                    task_id = excluded.task_id,
                    title = excluded.title,
                    author = excluded.author,
                    url = excluded.url,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    sales_user_id,
                    platform,
                    note.get("id"),
                    task_id,
                    note.get("title"),
                    note.get("author"),
                    note.get("url"),
                    json.dumps(note, ensure_ascii=False),
                    time.time(),
                ),
            )

    def save_comment(self, task_id: int, note_id: str, comment: dict[str, Any]) -> None:
        user_info = comment.get("user_info") or {}
        nickname = user_info.get("nickname") or user_info.get("nick_name") or comment.get("nickname") or comment.get("nick_name")
        user_id = user_info.get("user_id") or comment.get("user_id")
        platform = str(comment.get("platform") or "xhs")
        with self.connect() as conn:
            sales_user_id = self._task_owner(conn, task_id)
            conn.execute(
                """
                INSERT INTO comments(sales_user_id, platform, comment_id, task_id, note_id, nickname, user_id, content, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sales_user_id, comment_id) DO UPDATE SET
                    platform = excluded.platform,
                    task_id = excluded.task_id,
                    note_id = excluded.note_id,
                    nickname = excluded.nickname,
                    user_id = excluded.user_id,
                    content = excluded.content,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    sales_user_id,
                    platform,
                    comment.get("id"),
                    task_id,
                    note_id,
                    nickname,
                    user_id,
                    comment.get("content", ""),
                    json.dumps(comment, ensure_ascii=False),
                    time.time(),
                ),
            )

    def save_lead(self, task_id: int, lead: dict[str, Any]) -> None:
        platform = str(lead.get("platform") or "xhs")
        product_tags = ",".join(
            (p.get("tag", "") if isinstance(p, dict) else str(p))
            for p in lead.get("matched_products") or []
        )
        with self.connect() as conn:
            sales_user_id = self._task_owner(conn, task_id)
            conn.execute(
                """
                INSERT INTO leads(sales_user_id, platform, lead_id, task_id, note_id, comment_id, score, priority, product_tags,
                demand_summary, evidence, outreach_copy, feedback, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sales_user_id, lead_id) DO UPDATE SET
                    platform = excluded.platform,
                    task_id = excluded.task_id,
                    note_id = excluded.note_id,
                    comment_id = excluded.comment_id,
                    score = excluded.score,
                    priority = excluded.priority,
                    product_tags = excluded.product_tags,
                    demand_summary = excluded.demand_summary,
                    evidence = excluded.evidence,
                    outreach_copy = excluded.outreach_copy,
                    feedback = excluded.feedback,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    sales_user_id,
                    platform,
                    lead.get("lead_id"),
                    task_id,
                    lead.get("note_id"),
                    lead.get("comment_id"),
                    int(lead.get("score", 0)),
                    lead.get("priority"),
                    product_tags,
                    lead.get("demand_summary"),
                    lead.get("evidence"),
                    lead.get("outreach_copy"),
                    lead.get("feedback", ""),
                    json.dumps(lead, ensure_ascii=False),
                    time.time(),
                ),
            )

    def get_lead(self, lead_id: str, sales_user_id: int | None = None) -> dict[str, Any] | None:
        where = "lead_id = ?"
        params: list[Any] = [lead_id]
        if sales_user_id is not None:
            where += " AND sales_user_id = ?"
            params.append(int(sales_user_id))
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM leads WHERE {where} ORDER BY id DESC LIMIT 1", params).fetchone()
        return self._lead_to_dict(row) if row else None

    def get_comment(self, comment_id: str, sales_user_id: int | None = None) -> dict[str, Any] | None:
        where = "comment_id = ?"
        params: list[Any] = [comment_id]
        if sales_user_id is not None:
            where += " AND sales_user_id = ?"
            params.append(int(sales_user_id))
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM comments WHERE {where} ORDER BY id DESC LIMIT 1", params).fetchone()
        return self._comment_to_dict(row) if row else None

    def comment_identities(self, comment_ids: list[str], sales_user_id: int | None = None) -> dict[str, dict[str, str]]:
        ids = [str(item) for item in comment_ids if item]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        owner_filter = ""
        params: list[Any] = ids
        if sales_user_id is not None:
            owner_filter = " AND sales_user_id = ?"
            params = [*ids, int(sales_user_id)]
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT platform, comment_id, nickname, user_id FROM comments WHERE comment_id IN ({placeholders}){owner_filter}",
                params,
            ).fetchall()
        return {
            str(row["comment_id"]): {
                "platform": row["platform"] or "xhs",
                "nickname": row["nickname"] or "",
                "user_id": row["user_id"] or "",
            }
            for row in rows
        }

    def _lead_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = self._json_loads(row["payload"], {})
        payload.update({
            "lead_id": row["lead_id"],
            "task_id": row["task_id"],
            "sales_user_id": row["sales_user_id"],
            "salesUserId": row["sales_user_id"],
            "platform": row["platform"] or payload.get("platform") or "xhs",
            "note_id": row["note_id"] or payload.get("note_id", ""),
            "post_id": payload.get("post_id") or row["note_id"] or "",
            "comment_id": row["comment_id"] or payload.get("comment_id", ""),
            "score": int(row["score"] or payload.get("score") or 0),
            "priority": row["priority"] or payload.get("priority", ""),
            "demand_summary": row["demand_summary"] or payload.get("demand_summary", ""),
            "evidence": row["evidence"] or payload.get("evidence", ""),
            "outreach_copy": row["outreach_copy"] or payload.get("outreach_copy", ""),
            "feedback": row["feedback"] or payload.get("feedback", ""),
            "createdAt": row["created_at"],
        })
        return payload

    def _comment_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = self._json_loads(row["payload"], {})
        return {
            "commentId": row["comment_id"],
            "taskId": row["task_id"],
            "salesUserId": row["sales_user_id"],
            "platform": row["platform"] or payload.get("platform") or "xhs",
            "noteId": row["note_id"],
            "nickname": row["nickname"] or payload.get("nickname") or "",
            "userId": row["user_id"] or payload.get("user_id") or "",
            "content": row["content"] or payload.get("content") or "",
            "payload": payload,
            "createdAt": row["created_at"],
        }

    def save_prompt_run(self, task_id: int, prompt_version: str, model: str, input_summary: str, output_summary: str = "", error: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO prompt_runs(task_id, prompt_version, model, input_summary, output_summary, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, prompt_version, model, input_summary, output_summary, error, time.time()),
            )

    def save_agent_run(
        self,
        *,
        task_id: int,
        agent_key: str,
        agent_name: str,
        prompt_version: str,
        model: str,
        purpose: str,
        input_summary: str,
        output_summary: str,
        confidence: float,
        error: str,
        duration_ms: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs(task_id, agent_key, agent_name, prompt_version, model, purpose,
                input_summary, output_summary, confidence, error, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    agent_key,
                    agent_name,
                    prompt_version,
                    model,
                    purpose,
                    input_summary,
                    output_summary,
                    confidence,
                    error,
                    duration_ms,
                    time.time(),
                ),
            )

    def list_agent_runs(self, task_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if task_id:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE task_id = ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_runs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [self._agent_run_to_dict(row) for row in rows]

    def _agent_run_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        output = self._json_loads(row["output_summary"], None)
        return {
            "id": int(row["id"]),
            "taskId": int(row["task_id"]),
            "agentKey": row["agent_key"],
            "agentName": row["agent_name"],
            "promptVersion": row["prompt_version"] or "",
            "model": row["model"] or "",
            "purpose": row["purpose"] or "",
            "inputSummary": row["input_summary"] or "",
            "outputSummary": output if output is not None else (row["output_summary"] or ""),
            "confidence": float(row["confidence"] or 0.0),
            "error": row["error"] or "",
            "durationMs": int(row["duration_ms"] or 0),
            "createdAt": row["created_at"],
        }

    def ensure_prompt_version(
        self,
        *,
        agent_key: str,
        agent_name: str,
        version: str,
        prompt: str,
        status: str,
        score: float,
        notes: str = "",
        source_task_id: int | None = None,
    ) -> int:
        now = time.time()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM prompt_versions WHERE agent_key = ? AND version = ?",
                (agent_key, version),
            ).fetchone()
            if row:
                return int(row["id"])
            active = conn.execute(
                "SELECT id FROM prompt_versions WHERE agent_key = ? AND status = 'active'",
                (agent_key,),
            ).fetchone()
            insert_status = status if not active else ("candidate" if status == "active" else status)
            insert_sql = """
                INSERT INTO prompt_versions(agent_key, agent_name, version, status, score, prompt, notes,
                source_task_id, metrics, created_at, activated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            values = (
                agent_key,
                agent_name,
                version,
                insert_status,
                float(score),
                prompt,
                notes,
                source_task_id,
                "",
                now,
                now if insert_status == "active" else None,
            )
            cur = conn.execute(insert_sql, values)
            return int(cur.lastrowid)

    def create_prompt_candidate(
        self,
        *,
        agent_key: str,
        agent_name: str,
        base_version: str,
        prompt: str,
        source_task_id: int,
        notes: str,
    ) -> int:
        version = f"{base_version}-evo-{int(time.time())}"
        return self.ensure_prompt_version(
            agent_key=agent_key,
            agent_name=agent_name,
            version=version,
            prompt=prompt,
            status="candidate",
            score=0.0,
            notes=notes,
            source_task_id=source_task_id,
        )

    def get_active_prompt(self, agent_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM prompt_versions
                WHERE agent_key = ? AND status = 'active'
                ORDER BY activated_at DESC, id DESC
                LIMIT 1
                """,
                (agent_key,),
            ).fetchone()
        return self._prompt_to_dict(row) if row else None

    def list_prompt_versions(self, agent_key: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if agent_key:
                rows = conn.execute(
                    """
                    SELECT * FROM prompt_versions
                    WHERE agent_key = ?
                    ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
                    score DESC, id DESC
                    """,
                    (agent_key,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM prompt_versions
                    ORDER BY agent_key ASC,
                    CASE status WHEN 'active' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
                    score DESC, id DESC
                    """
                ).fetchall()
        return [self._prompt_to_dict(row) for row in rows]

    def promote_prompt_version(self, prompt_id: int) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (prompt_id,)).fetchone()
            if row is None:
                raise ValueError(f"Prompt 版本 {prompt_id} 不存在")
            agent_key = row["agent_key"]
            conn.execute(
                "UPDATE prompt_versions SET status = 'candidate' WHERE agent_key = ? AND status = 'active'",
                (agent_key,),
            )
            conn.execute(
                "UPDATE prompt_versions SET status = 'active', activated_at = ?, disabled_at = NULL WHERE id = ?",
                (now, prompt_id),
            )
        return self.get_active_prompt(agent_key) or {}

    def disable_prompt_version(self, prompt_id: int) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (prompt_id,)).fetchone()
            if row is None:
                raise ValueError(f"Prompt 版本 {prompt_id} 不存在")
            agent_key = row["agent_key"]
            was_active = row["status"] == "active"
            conn.execute(
                "UPDATE prompt_versions SET status = 'disabled', disabled_at = ? WHERE id = ?",
                (now, prompt_id),
            )
            if was_active:
                fallback = conn.execute(
                    """
                    SELECT id FROM prompt_versions
                    WHERE agent_key = ? AND status != 'disabled'
                    ORDER BY score DESC, activated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (agent_key,),
                ).fetchone()
                if fallback is None:
                    raise RuntimeError("不能禁用该 Agent 的最后一个可用 Prompt。")
                conn.execute(
                    "UPDATE prompt_versions SET status = 'active', activated_at = ?, disabled_at = NULL WHERE id = ?",
                    (now, int(fallback["id"])),
                )
        return self.get_active_prompt(agent_key) or {}

    def update_prompt_score(self, prompt_id: int, score: float, metrics: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE prompt_versions SET score = ?, metrics = ? WHERE id = ?",
                (float(score), json.dumps(metrics or {}, ensure_ascii=False), prompt_id),
            )

    def prompt_version_summary(self) -> list[dict[str, Any]]:
        versions = self.list_prompt_versions()
        summary: dict[str, dict[str, Any]] = {}
        for version in versions:
            item = summary.setdefault(
                version["agentKey"],
                {
                    "agentKey": version["agentKey"],
                    "agentName": version["agentName"],
                    "active": None,
                    "candidates": 0,
                    "disabled": 0,
                },
            )
            if version["status"] == "active":
                item["active"] = version
            elif version["status"] == "candidate":
                item["candidates"] += 1
            elif version["status"] == "disabled":
                item["disabled"] += 1
        return list(summary.values())

    def _prompt_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "agentKey": row["agent_key"],
            "agentName": row["agent_name"],
            "version": row["version"],
            "status": row["status"],
            "score": float(row["score"] or 0.0),
            "prompt": row["prompt"],
            "notes": row["notes"] or "",
            "sourceTaskId": row["source_task_id"],
            "metrics": self._json_loads(row["metrics"], {}),
            "createdAt": row["created_at"],
            "activatedAt": row["activated_at"],
            "disabledAt": row["disabled_at"],
        }

    def save_eval_case(self, task_id: int, case: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_cases(task_id, case_type, input_excerpt, expected_behavior, source_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    case.get("case_type", ""),
                    case.get("input_excerpt", ""),
                    case.get("expected_behavior", ""),
                    case.get("source_id", ""),
                    json.dumps(case, ensure_ascii=False),
                    time.time(),
                ),
            )

    def list_eval_cases(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_cases ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "taskId": row["task_id"],
                "caseType": row["case_type"] or "",
                "inputExcerpt": row["input_excerpt"] or "",
                "expectedBehavior": row["expected_behavior"] or "",
                "sourceId": row["source_id"] or "",
                "payload": self._json_loads(row["payload"], {}),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def save_eval_run(self, task_id: int, eval_type: str, agent_key: str, score: float, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_runs(task_id, eval_type, agent_key, score, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, eval_type, agent_key, float(score), json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def list_eval_runs(self, task_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM eval_runs WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "taskId": row["task_id"],
                "evalType": row["eval_type"] or "",
                "agentKey": row["agent_key"] or "",
                "score": float(row["score"] or 0.0),
                "payload": self._json_loads(row["payload"], {}),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def save_task_summary(self, task_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            sales_user_id = self._task_owner(conn, task_id)
            conn.execute(
                """
                INSERT INTO task_summaries(task_id, sales_user_id, summary, agent_summaries, evolution_summary, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    sales_user_id = excluded.sales_user_id,
                    summary = excluded.summary,
                    agent_summaries = excluded.agent_summaries,
                    evolution_summary = excluded.evolution_summary,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    task_id,
                    sales_user_id,
                    payload.get("task_summary") or payload.get("decision") or "",
                    json.dumps(payload.get("agent_summaries") or [], ensure_ascii=False),
                    payload.get("evolution_summary") or "",
                    json.dumps(payload, ensure_ascii=False),
                    time.time(),
                ),
            )

    def get_task_summary(self, task_id: int | None) -> dict[str, Any]:
        if not task_id:
            return {}
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM task_summaries WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return {}
        payload = self._json_loads(row["payload"], {})
        payload.setdefault("task_summary", row["summary"] or "")
        payload.setdefault("agent_summaries", self._json_loads(row["agent_summaries"], []))
        payload.setdefault("evolution_summary", row["evolution_summary"] or "")
        return payload

    def save_feedback(self, lead_id: str, feedback: str, sales_user_id: int | None = None) -> None:
        where = "lead_id = ?"
        params: list[Any] = [feedback]
        id_params: list[Any] = [lead_id]
        if sales_user_id is not None:
            where += " AND sales_user_id = ?"
            id_params.append(int(sales_user_id))
        with self.connect() as conn:
            conn.execute(f"UPDATE leads SET feedback = ? WHERE {where}", [*params, *id_params])

    def create_lead_feedback(
        self,
        *,
        task_id: int | None,
        sales_user_id: int | None = None,
        lead_id: str = "",
        comment_id: str = "",
        feedback_type: str,
        feedback: str = "",
        user_note: str = "",
        evidence: str = "",
        expected_result: str = "",
    ) -> int:
        now = time.time()
        with self.connect() as conn:
            owner_id = int(sales_user_id if sales_user_id is not None else self._task_owner(conn, task_id))
            sql = """
                INSERT INTO lead_feedback(sales_user_id, task_id, lead_id, comment_id, feedback_type, feedback, user_note,
                evidence, expected_result, status, analysis_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            values = (
                owner_id,
                task_id,
                lead_id,
                comment_id,
                feedback_type,
                feedback,
                user_note,
                evidence,
                expected_result,
                "pending",
                "",
                now,
                now,
            )
            cur = conn.execute(sql, values)
            return int(cur.lastrowid)

    def get_lead_feedback(self, feedback_id: int, sales_user_id: int | None = None) -> dict[str, Any] | None:
        where = "id = ?"
        params: list[Any] = [feedback_id]
        if sales_user_id is not None:
            where += " AND sales_user_id = ?"
            params.append(int(sales_user_id))
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM lead_feedback WHERE {where}", params).fetchone()
        return self._lead_feedback_to_dict(row) if row else None

    def list_lead_feedback(
        self,
        status: str | None = None,
        limit: int = 100,
        sales_user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        filters = []
        params: list[Any] = []
        if status:
            filters.append("status = ?")
            params.append(status)
        if sales_user_id is not None:
            filters.append("sales_user_id = ?")
            params.append(int(sales_user_id))
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM lead_feedback
                {where}
                ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'analyzed' THEN 1 WHEN 'applied' THEN 2 ELSE 3 END,
                updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._lead_feedback_to_dict(row) for row in rows]

    def save_lead_feedback_analysis(
        self,
        feedback_id: int,
        analysis: dict[str, Any],
        *,
        candidate_prompt_id: int | None = None,
        target_agent_key: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE lead_feedback
                SET status = 'analyzed', analysis_json = ?, candidate_prompt_id = COALESCE(?, candidate_prompt_id),
                target_agent_key = COALESCE(NULLIF(?, ''), target_agent_key), updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(analysis, ensure_ascii=False), candidate_prompt_id, target_agent_key, time.time(), feedback_id),
            )

    def mark_lead_feedback_applied(self, feedback_id: int, candidate_prompt_id: int, target_agent_key: str) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE lead_feedback
                SET status = 'applied', candidate_prompt_id = ?, target_agent_key = ?, updated_at = ?, applied_at = ?
                WHERE id = ?
                """,
                (candidate_prompt_id, target_agent_key, now, now, feedback_id),
            )

    def dismiss_lead_feedback(self, feedback_id: int) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE lead_feedback
                SET status = 'dismissed', updated_at = ?, dismissed_at = ?
                WHERE id = ?
                """,
                (now, now, feedback_id),
            )

    def _lead_feedback_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "salesUserId": row["sales_user_id"],
            "taskId": row["task_id"],
            "leadId": row["lead_id"] or "",
            "commentId": row["comment_id"] or "",
            "feedbackType": row["feedback_type"] or "",
            "feedback": row["feedback"] or "",
            "userNote": row["user_note"] or "",
            "evidence": row["evidence"] or "",
            "expectedResult": row["expected_result"] or "",
            "status": row["status"] or "",
            "analysis": self._json_loads(row["analysis_json"], {}),
            "candidatePromptId": row["candidate_prompt_id"],
            "targetAgentKey": row["target_agent_key"] or "",
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "appliedAt": row["applied_at"],
            "dismissedAt": row["dismissed_at"],
        }
