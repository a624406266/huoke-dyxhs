#!/usr/bin/env python3
"""Local open lead workbench backend."""
from __future__ import annotations

import json
import os
import threading
import time
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import config_loader
from agent_harness import HarnessPipeline
from agent_harness.evaluator import HarnessEvaluator
from agent_harness.feedback import FEEDBACK_TYPE_LABELS, FeedbackLearningService
from agent_harness.registry import PromptRegistry
from config_loader import load_config
from lead_pipeline import public_config
from product_catalog import product_distribution
from sales_scoring import redact_text
from storage import SalesStorage, hash_password, is_password_hash
from xhs_adapter import XhsAdapter
from xhs_login_helper import XhsLoginHelper


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "assets"
CONFIG = load_config()
STORAGE_CONFIG = CONFIG.get("storage") or {}
STORAGE = SalesStorage(STORAGE_CONFIG.get("db_path", ROOT / "data" / "sales_leads.db"))
STORAGE.mark_stale_running_as_interrupted()
LOGIN_HELPERS: dict[int, XhsLoginHelper] = {}
LOCAL_SALES_USER_ID = 0


ADMIN_GET_PATHS = {
    "/api/agents",
    "/api/prompts",
    "/api/settings",
    "/api/feedback/list",
    "/api/status",
}
ADMIN_POST_PATHS = {
    "/api/admin/tasks/list",
    "/api/admin/tasks/status",
    "/api/admin/tasks/audit",
    "/api/feedback/analyze",
    "/api/feedback/apply",
    "/api/feedback/dismiss",
    "/api/settings",
    "/api/prompts/promote",
    "/api/prompts/disable",
    "/api/evals/run",
}
LOCAL_GET_PATHS = {
    "/api/tasks/list",
    "/api/xhs_login/status",
}
LOCAL_POST_PATHS = {
    "/api/search",
    "/api/leads",
    "/api/tasks/start",
    "/api/tasks/status",
    "/api/tasks/list",
    "/api/tasks/stop",
    "/api/batch_task/start",
    "/api/batch_task/status",
    "/api/batch_task/stop",
    "/api/xhs_login/start",
    "/api/xhs_login/status",
    "/api/xhs_login/confirm",
    "/api/feedback",
}
PUBLIC_GET_PATHS = {
    "/api/config",
    "/api/auth/me",
    "/api/setup/status",
}
PUBLIC_POST_PATHS = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/setup/admin",
}


def bootstrap_auth() -> None:
    auth = CONFIG.get("auth") or {}
    admin_hash = str(auth.get("admin_password_hash") or "").strip()
    raw_admin_password = os.environ.get("XHS_ADMIN_PASSWORD") or os.environ.get("HARNESS_ADMIN_PASSWORD")
    if not admin_hash and raw_admin_password:
        admin_hash = hash_password(raw_admin_password)
    if admin_hash:
        if not is_password_hash(admin_hash):
            raise RuntimeError("管理员密码配置必须是 PBKDF2 哈希；请使用 XHS_ADMIN_PASSWORD_HASH/HARNESS_ADMIN_PASSWORD_HASH。")
        STORAGE.ensure_admin_user(
            str(auth.get("admin_username") or "admin"),
            admin_hash,
            str(auth.get("admin_display_name") or "管理员"),
        )
    STORAGE.delete_expired_sessions()


bootstrap_auth()


def reload_config() -> None:
    global CONFIG
    CONFIG = load_config()


def xhs_home(sales_user_id: int | None = None) -> Path | None:
    if sales_user_id is None:
        return None
    return ROOT / "data" / "xhs-profiles" / str(int(sales_user_id))


def get_adapter(sales_user_id: int | None = None) -> XhsAdapter:
    return XhsAdapter(CONFIG, ROOT, profile_dir=xhs_home(sales_user_id))


def get_pipeline(sales_user_id: int | None = None) -> HarnessPipeline:
    return HarnessPipeline(get_adapter(sales_user_id), CONFIG, STORAGE)


def get_login_helper(sales_user_id: int) -> XhsLoginHelper:
    helper = LOGIN_HELPERS.get(int(sales_user_id))
    if helper is None:
        helper = XhsLoginHelper(ROOT, home_dir=xhs_home(sales_user_id))
        LOGIN_HELPERS[int(sales_user_id)] = helper
    return helper


def xhs_login_status_payload(sales_user_id: int) -> dict[str, Any]:
    helper_data = get_login_helper(sales_user_id).status()
    helper_mode = str(helper_data.get("mode") or "")
    helper_status = str(helper_data.get("status") or "")
    if helper_mode == "qrcode" and helper_status in {"waiting", "scanned", "confirmed", "expired", "error"}:
        return {"helper": helper_data}
    try:
        return {"helper": helper_data, "xhs": get_adapter(sales_user_id).status()}
    except Exception as exc:
        return {"helper": helper_data, "xhsError": str(exc)}


def get_registry() -> PromptRegistry:
    registry = PromptRegistry(STORAGE)
    registry.ensure_defaults()
    return registry


def get_feedback_service() -> FeedbackLearningService:
    return FeedbackLearningService(CONFIG, STORAGE)


def clamp_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return min(max(number, min_value), max_value)


def clamp_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return min(max(number, min_value), max_value)


class TaskManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_threads: dict[int, threading.Thread] = {}
        self.stop_flags: dict[int, bool] = {}
        self.task_sales_users: dict[int, int] = {}

    def start_task(self, payload: dict[str, Any], mode: str = "single", sales_user_id: int | None = None) -> dict[str, Any]:
        owner_id = int(sales_user_id or 0)
        with self.lock:
            self._cleanup_finished_locked()
            if self._current_task_id_locked(owner_id):
                raise RuntimeError("已有扫描任务正在运行，请先停止或等待完成。")
            task_id = STORAGE.create_task(payload, mode=mode, status_text="任务已创建，准备启动后台扫描。", sales_user_id=owner_id)
            self.stop_flags[task_id] = False
            self.task_sales_users[task_id] = owner_id
            thread = threading.Thread(target=self._run_task, args=(task_id, dict(payload), mode, owner_id), daemon=True)
            self.active_threads[task_id] = thread
            STORAGE.add_task_event(task_id, "info", "Task Manager", "任务已创建，后台线程即将启动。", {"mode": mode})
            thread.start()
        return self.get_status(task_id, sales_user_id=owner_id)

    def stop_task(self, task_id: int | None = None, sales_user_id: int | None = None) -> dict[str, Any]:
        owner_id = int(sales_user_id or 0)
        with self.lock:
            target_id = task_id or self._current_task_id_locked(owner_id)
            if not target_id:
                return {"ok": True, "message": "当前没有正在运行的任务。"}
            if not STORAGE.get_task(target_id, sales_user_id=owner_id):
                return {"ok": False, "error": f"任务 {target_id} 不存在"}
            self.stop_flags[target_id] = True
        STORAGE.add_task_event(target_id, "warning", "Task Manager", "已收到停止指令，当前只读步骤结束后会停止。")
        STORAGE.update_task(target_id, status_text="已收到停止指令，当前只读步骤结束后会停止。")
        return self.get_status(target_id, sales_user_id=owner_id)

    def get_status(self, task_id: int | None = None, sales_user_id: int | None = None) -> dict[str, Any]:
        owner_id = int(sales_user_id) if sales_user_id is not None else None
        with self.lock:
            self._cleanup_finished_locked()
            target_id = task_id or (self._current_task_id_locked(owner_id) if owner_id is not None else None)
        if not target_id:
            latest = STORAGE.list_tasks(1, sales_user_id=owner_id)
            if not latest:
                return {"ok": True, "hasTask": False, "tasks": []}
            target_id = int(latest[0]["id"])
        task = STORAGE.get_task(target_id, sales_user_id=owner_id)
        if not task:
            return {"ok": False, "hasTask": False, "error": f"任务 {target_id} 不存在"}
        is_running = self._is_thread_running(target_id) and task["status"] == "running"
        result_owner_id = int(task.get("salesUserId") or 0) if owner_id is None else owner_id
        result = hydrate_result_lead_users(task.get("result") or {}, result_owner_id)
        progress = task.get("progress") or {}
        leads = result.get("leads") or {
            "keyword": task.get("keyword", ""),
            "scannedNotesCount": task.get("notesCount", 0),
            "totalCommentsScanned": 0,
            "leadsCount": task.get("leadsCount", 0),
            "leads": [],
            "productDistribution": [],
        }
        return {
            "ok": True,
            "hasTask": True,
            "taskId": task["id"],
            "status": task["status"],
            "isRunning": is_running,
            "mode": task["mode"],
            "statusText": task["statusText"],
            "errorText": task["error"],
            "progress": progress,
            "events": task.get("events") or [],
            "notes": result.get("notes") or [],
            "leads": leads,
            "report": result.get("report"),
            "demandDescription": result.get("demandDescription") or task.get("demandDescription") or (task.get("payload") or {}).get("demandDescription", ""),
            "agentTrace": result.get("agentTrace") or STORAGE.list_agent_runs(target_id),
            "taskSummary": result.get("taskSummary") or STORAGE.get_task_summary(target_id),
            "evolutionSummary": result.get("evolutionSummary") or (STORAGE.get_task_summary(target_id).get("evolution_summary") if STORAGE.get_task_summary(target_id) else ""),
            "promptVersionSummary": result.get("promptVersionSummary") or STORAGE.prompt_version_summary(),
            "auditTrail": result.get("auditTrail") or [],
            "result": result,
            "currentRound": int(progress.get("currentRound") or 0),
            "totalRounds": int(progress.get("totalRounds") or (1 if task["mode"] == "single" else 0)),
            "startedAt": task["startedAt"],
            "updatedAt": task["updatedAt"],
            "finishedAt": task["finishedAt"],
        }

    def list_tasks(self, limit: int = 20, sales_user_id: int | None = None) -> dict[str, Any]:
        owner_id = int(sales_user_id) if sales_user_id is not None else None
        return {"ok": True, "tasks": STORAGE.list_tasks(limit, sales_user_id=owner_id)}

    def emit(self, task_id: int, level: str, agent: str, message: str, data: Any | None = None) -> None:
        STORAGE.add_task_event(task_id, level, agent, message, data)
        STORAGE.update_task(task_id, status_text=message)

    def update_progress(self, task_id: int, progress: dict[str, Any]) -> None:
        STORAGE.update_task(task_id, progress=progress)

    def _run_task(self, task_id: int, payload: dict[str, Any], mode: str, sales_user_id: int) -> None:
        result: dict[str, Any] = {}
        status = "completed"
        error = ""
        status_text = "任务已完成。"
        try:
            self.emit(task_id, "info", "Task Manager", "后台任务已启动。")
            if mode == "batch":
                result = self._run_batch(task_id, payload, sales_user_id)
                if self._is_stop_requested(task_id):
                    status = "stopped"
                    status_text = "任务已按用户要求停止。"
                else:
                    status_text = "全部扫描轮次已完成。"
            else:
                result = self._run_single(task_id, payload, sales_user_id)
                if self._is_stop_requested(task_id):
                    status = "stopped"
                    status_text = "任务已按用户要求停止。"
            self.emit(task_id, "success", "Task Manager", status_text)
        except Exception as exc:
            status = "failed"
            error = str(exc)
            status_text = f"任务异常终止：{error}"
            self.emit(task_id, "error", "Task Manager", status_text)
        finally:
            STORAGE.finish_task(task_id, status=status, error=error[:1000], result=result or None, status_text=status_text)
            with self.lock:
                self.active_threads.pop(task_id, None)
                self.stop_flags.pop(task_id, None)
                self.task_sales_users.pop(task_id, None)

    def _run_single(self, task_id: int, payload: dict[str, Any], sales_user_id: int) -> dict[str, Any]:
        STORAGE.update_task(task_id, progress={"stage": "starting", "percent": 1, "currentRound": 1, "totalRounds": 1})
        result = get_pipeline(sales_user_id).run(
            payload,
            task_id=task_id,
            page=1,
            stop_checker=lambda: self._is_stop_requested(task_id),
            event_callback=lambda level, agent, message, data=None: self.emit(task_id, level, agent, message, data),
            progress_callback=lambda data: self.update_progress(task_id, {**data, "currentRound": 1, "totalRounds": 1}),
        )
        result["taskId"] = task_id
        STORAGE.update_task(task_id, result=result, progress={"stage": "done", "percent": 100, "currentRound": 1, "totalRounds": 1})
        return result

    def _run_batch(self, task_id: int, payload: dict[str, Any], sales_user_id: int) -> dict[str, Any]:
        total_rounds = clamp_int(payload.get("rounds"), 3, 1, 10)
        interval_min = clamp_float(payload.get("intervalMin"), 30.0, 1.0, 180.0)
        interval_max = clamp_float(payload.get("intervalMax"), 60.0, interval_min, 180.0)
        notes_map: dict[str, dict[str, Any]] = {}
        leads_map: dict[str, dict[str, Any]] = {}
        total_comments = 0
        latest_report: dict[str, Any] | None = None
        audit_trail: list[dict[str, Any]] = []
        snapshot: dict[str, Any] = {}

        for round_index in range(1, total_rounds + 1):
            if self._is_stop_requested(task_id):
                break
            self.emit(task_id, "info", "Task Manager", f"开始第 {round_index}/{total_rounds} 轮扫描。")
            self.update_progress(task_id, {"stage": "round", "percent": int((round_index - 1) * 100 / total_rounds), "currentRound": round_index, "totalRounds": total_rounds})
            result = get_pipeline(sales_user_id).run(
                payload,
                task_id=task_id,
                page=round_index,
                stop_checker=lambda: self._is_stop_requested(task_id),
                event_callback=lambda level, agent, message, data=None: self.emit(task_id, level, agent, message, data),
                progress_callback=lambda data, current_round=round_index: self.update_progress(task_id, {**data, "currentRound": current_round, "totalRounds": total_rounds}),
            )
            for note in result.get("notes") or []:
                notes_map[note["id"]] = note
            for lead in ((result.get("leads") or {}).get("leads") or []):
                leads_map[lead["lead_id"]] = lead
            total_comments += int((result.get("leads") or {}).get("totalCommentsScanned", 0))
            latest_report = result.get("report")
            audit_trail.extend(result.get("auditTrail") or [])
            snapshot = self._batch_snapshot(task_id, payload, notes_map, leads_map, total_comments, latest_report, audit_trail, result)
            STORAGE.update_task(task_id, result=snapshot)

            if round_index < total_rounds and not self._is_stop_requested(task_id):
                interval_sec = self._sleep_interval(interval_min, interval_max)
                deadline = time.time() + interval_sec
                self.emit(task_id, "info", "Task Manager", f"第 {round_index} 轮完成，休眠约 {int(interval_sec)} 秒后继续。")
                while time.time() < deadline:
                    if self._is_stop_requested(task_id):
                        break
                    remaining = max(0, int(deadline - time.time()))
                    STORAGE.update_task(task_id, status_text=f"第 {round_index} 轮完成，休眠 {remaining} 秒后继续。")
                    time.sleep(min(5, remaining or 1))

        return snapshot or self._batch_snapshot(task_id, payload, notes_map, leads_map, total_comments, latest_report, audit_trail, {})

    def _batch_snapshot(
        self,
        task_id: int,
        payload: dict[str, Any],
        notes_map: dict[str, dict[str, Any]],
        leads_map: dict[str, dict[str, Any]],
        total_comments: int,
        latest_report: dict[str, Any] | None,
        audit_trail: list[dict[str, Any]],
        latest_result: dict[str, Any],
    ) -> dict[str, Any]:
        notes = [dict(note) for note in notes_map.values()]
        leads = sorted([dict(lead) for lead in leads_map.values()], key=lambda item: int(item.get("score", 0)), reverse=True)
        return {
            "taskId": task_id,
            "keyword": payload.get("keyword", ""),
            "demandDescription": payload.get("demandDescription", ""),
            "businessDirection": payload.get("businessDirection", ""),
            "metric": payload.get("metric", "engagement"),
            "metricLabel": latest_result.get("metricLabel", ""),
            "count": len(notes),
            "notes": notes,
            "leads": {
                "keyword": payload.get("keyword", ""),
                "scannedNotesCount": len(notes),
                "totalCommentsScanned": total_comments,
                "leadsCount": len(leads),
                "leads": leads,
                "productDistribution": product_distribution(leads),
            },
            "report": latest_report,
            "auditTrail": audit_trail[-100:],
            "agentTrace": latest_result.get("agentTrace", []),
            "taskSummary": latest_result.get("taskSummary", {}),
            "evolutionSummary": latest_result.get("evolutionSummary", ""),
            "promptVersionSummary": latest_result.get("promptVersionSummary", []),
            "safety": latest_result.get("safety", {}),
        }

    def _sleep_interval(self, interval_min: float, interval_max: float) -> float:
        import random

        return random.uniform(interval_min * 60.0, interval_max * 60.0)

    def _is_stop_requested(self, task_id: int) -> bool:
        with self.lock:
            return bool(self.stop_flags.get(task_id))

    def _is_thread_running(self, task_id: int) -> bool:
        with self.lock:
            thread = self.active_threads.get(task_id)
            return bool(thread and thread.is_alive())

    def _current_task_id_locked(self, sales_user_id: int | None = None) -> int | None:
        owner_id = int(sales_user_id or 0)
        for task_id, thread in self.active_threads.items():
            if thread.is_alive() and self.task_sales_users.get(task_id, 0) == owner_id:
                return task_id
        return None

    def _cleanup_finished_locked(self) -> None:
        finished = [task_id for task_id, thread in self.active_threads.items() if not thread.is_alive()]
        for task_id in finished:
            self.active_threads.pop(task_id, None)
            self.stop_flags.pop(task_id, None)
            self.task_sales_users.pop(task_id, None)


task_manager = TaskManager()


def hydrate_result_lead_users(result: dict[str, Any], sales_user_id: int | None = None) -> dict[str, Any]:
    leads_container = result.get("leads") or {}
    leads = leads_container.get("leads") or []
    if not leads:
        return result
    identities = STORAGE.comment_identities([str(lead.get("comment_id") or "") for lead in leads], sales_user_id=sales_user_id)
    redact_user = bool((CONFIG.get("sales") or {}).get("redact_user", False))
    for lead in leads:
        comment_id = str(lead.get("comment_id") or "")
        identity = identities.get(comment_id) or {}
        nickname = identity.get("nickname") or lead.get("raw_nickname") or lead.get("nickname") or ""
        user_id = identity.get("user_id") or lead.get("raw_user_id") or lead.get("user_id") or ""
        if identity:
            lead["raw_nickname"] = identity.get("nickname") or ""
            lead["raw_user_id"] = identity.get("user_id") or ""
        if redact_user:
            lead["nickname"] = redact_text(nickname, 1) if nickname else ""
            lead["user_id"] = redact_text(user_id, 4) if user_id else ""
        else:
            lead["nickname"] = nickname
            lead["user_id"] = user_id
    return result


def normalize_feedback_type(value: str, fallback_feedback: str = "") -> str:
    value = str(value or "").strip()
    if value in FEEDBACK_TYPE_LABELS:
        return value
    feedback = str(fallback_feedback or "").strip()
    mapping = {
        "有效": "valid_lead",
        "已跟进": "valid_lead",
        "已成交": "valid_lead",
        "无效": "invalid_lead",
        "产品不准": "wrong_product",
        "话术不准": "bad_outreach",
        "漏识别": "missed_lead",
    }
    return mapping.get(feedback, "other")


def default_expected_result(feedback_type: str) -> str:
    return {
        "valid_lead": "类似评论应保持为可跟进线索，并保留原文证据。",
        "invalid_lead": "类似评论应降低线索置信度或标记为忽略。",
        "missed_lead": "类似评论应识别为可跟进线索，并说明需求阶段与证据。",
        "wrong_product": "类似线索应匹配更准确的产品方向。",
        "bad_outreach": "类似线索应生成更低打扰、更贴合证据的人工跟进话术。",
        "other": "请根据反馈改进对应 Agent 的判断。",
    }.get(feedback_type, "请根据反馈改进对应 Agent 的判断。")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def auth_token(self) -> str:
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        explicit = self.headers.get("X-Session-Token", "")
        if explicit:
            return explicit.strip()
        raw_cookie = self.headers.get("Cookie", "")
        if raw_cookie:
            cookie = SimpleCookie()
            cookie.load(raw_cookie)
            if "xhs_session" in cookie:
                return cookie["xhs_session"].value
        return ""

    def current_session(self) -> dict[str, Any] | None:
        cached = getattr(self, "_current_session", None)
        if cached is not None:
            return cached
        session = STORAGE.get_session(self.auth_token())
        setattr(self, "_current_session", session)
        return session

    def require_role(self, role: str) -> dict[str, Any] | None:
        session = self.current_session()
        if not session:
            self.respond_json({"ok": False, "error": "未登录或会话已过期。"}, status=401)
            return None
        if session.get("role") != role:
            self.respond_json({"ok": False, "error": "权限不足。"}, status=403)
            return None
        return session

    def require_admin(self) -> dict[str, Any] | None:
        return self.require_role("admin")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/config":
            self.respond_json({"ok": True, "data": public_config()})
            return
        if path == "/api/setup/status":
            self.respond_json({"ok": True, "data": {"hasAdmin": STORAGE.has_admin_user()}})
            return
        if path == "/api/auth/me":
            session = self.current_session()
            self.respond_json({"ok": True, "data": {"user": (session or {}).get("user") if session else None}})
            return
        if path in ADMIN_GET_PATHS:
            if not self.require_admin():
                return
        if path == "/api/settings":
            self.respond_json({"ok": True, "data": config_loader.public_settings(CONFIG)})
            return
        if path == "/api/agents":
            self.respond_json({"ok": True, "data": {"agents": get_registry().agents_payload()}})
            return
        if path == "/api/prompts":
            self.respond_json({"ok": True, "data": {"prompts": get_registry().prompt_versions(), "summary": STORAGE.prompt_version_summary()}})
            return
        if path == "/api/feedback/list":
            params = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item) if parsed.query else {}
            status = params.get("status") or None
            limit = clamp_int(params.get("limit"), 100, 1, 200)
            self.respond_json({"ok": True, "data": get_feedback_service().list_feedback(status=status, limit=limit)})
            return
        if path == "/api/status":
            try:
                adapter = get_adapter()
                status = adapter.status()
                self.respond_json({"ok": True, "data": {"xhs": status, "command": adapter.command}})
            except Exception as exc:
                self.respond_json({"ok": False, "error": str(exc)}, status=400)
            return
        if path == "/api/tasks/list":
            self.respond_json({"ok": True, "data": task_manager.list_tasks(sales_user_id=LOCAL_SALES_USER_ID)})
            return
        if path == "/api/xhs_login/status":
            self.respond_json({"ok": True, "data": xhs_login_status_payload(LOCAL_SALES_USER_ID)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        allowed = PUBLIC_POST_PATHS | ADMIN_POST_PATHS | LOCAL_POST_PATHS
        if path not in allowed:
            if path.startswith("/api/"):
                self.respond_json({"ok": False, "error": "接口不存在。"}, status=404)
            else:
                self.send_error(404)
            return
        try:
            payload = self.read_payload()
            if path == "/api/setup/admin":
                if STORAGE.has_admin_user():
                    self.respond_json({"ok": False, "error": "管理员已初始化。"}, status=409)
                    return
                username = str(payload.get("username") or "admin").strip() or "admin"
                password = str(payload.get("password") or "")
                if len(password) < 6:
                    raise ValueError("管理员密码至少需要 6 位。")
                user = STORAGE.ensure_admin_user(username, hash_password(password), str(payload.get("displayName") or "管理员"))
                ttl = int((CONFIG.get("auth") or {}).get("session_ttl_seconds") or 86400)
                session = STORAGE.create_session(user["id"], ttl_seconds=ttl)
                self.respond_json(
                    {"ok": True, "data": session},
                    headers={"Set-Cookie": f"xhs_session={session['token']}; Path=/; HttpOnly; SameSite=Lax"},
                )
                return
            if path == "/api/auth/login":
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                role = str(payload.get("role") or "").strip().lower() or None
                if role not in {None, "admin"}:
                    raise ValueError("role 不合法")
                user = STORAGE.authenticate_user(username, password, role=role)
                if not user:
                    self.respond_json({"ok": False, "error": "用户名或密码错误，或账号已禁用。"}, status=401)
                    return
                ttl = int((CONFIG.get("auth") or {}).get("session_ttl_seconds") or 86400)
                session = STORAGE.create_session(user["id"], ttl_seconds=ttl)
                self.respond_json(
                    {"ok": True, "data": session},
                    headers={"Set-Cookie": f"xhs_session={session['token']}; Path=/; HttpOnly; SameSite=Lax"},
                )
                return
            if path == "/api/auth/logout":
                STORAGE.revoke_session(self.auth_token())
                self.respond_json(
                    {"ok": True, "data": {"loggedOut": True}},
                    headers={"Set-Cookie": "xhs_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"},
                )
                return

            sales_user_id = LOCAL_SALES_USER_ID
            if path in ADMIN_POST_PATHS:
                if not self.require_admin():
                    return

            if path == "/api/admin/tasks/list":
                result = task_manager.list_tasks(clamp_int(payload.get("limit"), 20, 1, 100), sales_user_id=None)
            elif path == "/api/admin/tasks/status":
                result = task_manager.get_status(self.task_id_from_payload(payload), sales_user_id=None)
            elif path == "/api/admin/tasks/audit":
                task_id = self.task_id_from_payload(payload)
                if not task_id:
                    raise ValueError("taskId 不能为空")
                result = STORAGE.list_audit_decisions(
                    task_id,
                    stage=str(payload.get("stage") or "").strip(),
                    entity_type=str(payload.get("entityType") or payload.get("entity_type") or "").strip(),
                    decision=str(payload.get("decision") or "").strip(),
                    keyword=str(payload.get("keyword") or "").strip(),
                    limit=clamp_int(payload.get("limit"), 100, 1, 200),
                    offset=clamp_int(payload.get("offset"), 0, 0, 1000000),
                )
            elif path == "/api/tasks/start":
                mode = "batch" if payload.get("mode") == "batch" or payload.get("batchMode") else "single"
                result = task_manager.start_task(payload, mode=mode, sales_user_id=sales_user_id)
            elif path == "/api/search":
                result = task_manager.start_task(payload, mode="single", sales_user_id=sales_user_id)
            elif path == "/api/leads":
                payload["scanLeads"] = True
                result = task_manager.start_task(payload, mode="single", sales_user_id=sales_user_id)
            elif path == "/api/tasks/status":
                result = task_manager.get_status(self.task_id_from_payload(payload), sales_user_id=sales_user_id)
            elif path == "/api/tasks/list":
                result = task_manager.list_tasks(clamp_int(payload.get("limit"), 20, 1, 100), sales_user_id=sales_user_id)
            elif path == "/api/tasks/stop":
                result = task_manager.stop_task(self.task_id_from_payload(payload), sales_user_id=sales_user_id)
            elif path == "/api/xhs_login/start":
                login_mode = str(payload.get("mode") or "").strip().lower()
                result = get_login_helper(int(sales_user_id or 0)).start(mode=login_mode or None)
            elif path == "/api/xhs_login/status":
                result = xhs_login_status_payload(int(sales_user_id or 0))
            elif path == "/api/xhs_login/confirm":
                exported = get_login_helper(int(sales_user_id or 0)).confirm()
                result = {"exported": exported, "xhs": get_adapter(sales_user_id).status()}
            elif path == "/api/batch_task/start":
                result = task_manager.start_task(payload, mode="batch", sales_user_id=sales_user_id)
            elif path == "/api/batch_task/status":
                result = task_manager.get_status(self.task_id_from_payload(payload), sales_user_id=sales_user_id)
            elif path == "/api/batch_task/stop":
                result = task_manager.stop_task(self.task_id_from_payload(payload), sales_user_id=sales_user_id)
            elif path == "/api/feedback":
                lead_id = str(payload.get("leadId") or "")
                feedback = str(payload.get("feedback") or "")
                feedback_type = normalize_feedback_type(str(payload.get("feedbackType") or ""), feedback)
                if feedback_type not in FEEDBACK_TYPE_LABELS:
                    raise ValueError("feedbackType 不合法")
                lead = STORAGE.get_lead(lead_id, sales_user_id=sales_user_id) if lead_id else None
                comment_id = str(payload.get("commentId") or (lead or {}).get("comment_id") or "")
                comment = STORAGE.get_comment(comment_id, sales_user_id=sales_user_id) if comment_id else None
                task_id = self.task_id_from_payload(payload) or (int((lead or {}).get("task_id") or 0) or None)
                if task_id and not STORAGE.get_task(task_id, sales_user_id=sales_user_id):
                    raise ValueError("任务不存在。")
                evidence = str(payload.get("evidence") or (lead or {}).get("evidence") or (comment or {}).get("content") or "")
                expected_result = str(payload.get("expectedResult") or default_expected_result(feedback_type))
                user_note = str(payload.get("userNote") or payload.get("note") or "")
                if lead_id and feedback:
                    STORAGE.save_feedback(lead_id, feedback, sales_user_id=sales_user_id)
                if not task_id:
                    raise ValueError("taskId 不能为空；漏识别反馈需要绑定当前任务。")
                if not evidence:
                    raise ValueError("反馈证据不能为空。")
                feedback_id = STORAGE.create_lead_feedback(
                    task_id=task_id,
                    sales_user_id=sales_user_id,
                    lead_id=lead_id,
                    comment_id=comment_id,
                    feedback_type=feedback_type,
                    feedback=feedback or FEEDBACK_TYPE_LABELS.get(feedback_type, feedback_type),
                    user_note=user_note,
                    evidence=evidence,
                    expected_result=expected_result,
                )
                result = {"ok": True, "feedbackId": feedback_id, "feedback": STORAGE.get_lead_feedback(feedback_id, sales_user_id=sales_user_id)}
            elif path == "/api/feedback/analyze":
                feedback_id = int(payload.get("feedbackId") or payload.get("id") or 0)
                if not feedback_id:
                    raise ValueError("feedbackId 不能为空")
                result = get_feedback_service().analyze(feedback_id)
            elif path == "/api/feedback/apply":
                feedback_id = int(payload.get("feedbackId") or payload.get("id") or 0)
                if not feedback_id:
                    raise ValueError("feedbackId 不能为空")
                result = get_feedback_service().apply(feedback_id)
            elif path == "/api/feedback/dismiss":
                feedback_id = int(payload.get("feedbackId") or payload.get("id") or 0)
                if not feedback_id:
                    raise ValueError("feedbackId 不能为空")
                result = get_feedback_service().dismiss(feedback_id)
            elif path == "/api/settings":
                config_loader.update_local_settings(payload)
                reload_config()
                result = config_loader.public_settings(CONFIG)
            elif path == "/api/prompts/promote":
                prompt_id = int(payload.get("promptId") or payload.get("id") or 0)
                if not prompt_id:
                    raise ValueError("promptId 不能为空")
                result = get_registry().promote(prompt_id)
            elif path == "/api/prompts/disable":
                prompt_id = int(payload.get("promptId") or payload.get("id") or 0)
                if not prompt_id:
                    raise ValueError("promptId 不能为空")
                result = get_registry().disable(prompt_id)
            elif path == "/api/evals/run":
                task_id = self.task_id_from_payload(payload) or self.latest_task_id()
                if not task_id:
                    raise ValueError("没有可评估的任务")
                result = HarnessEvaluator(STORAGE).evaluate_task(task_id)
            else:
                self.send_error(404)
                return
            self.respond_json({"ok": True, "data": result})
        except Exception as exc:
            self.respond_json({"ok": False, "error": str(exc)}, status=400)

    def task_id_from_payload(self, payload: dict[str, Any]) -> int | None:
        task_id = payload.get("taskId") or payload.get("id")
        if task_id in (None, ""):
            return None
        return int(task_id)

    def latest_task_id(self) -> int | None:
        latest = STORAGE.list_tasks(1)
        if not latest:
            return None
        return int(latest[0]["id"])

    def read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def respond_json(self, data: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    host = str((CONFIG.get("server") or {}).get("host", "127.0.0.1"))
    port = int((CONFIG.get("server") or {}).get("port", os.environ.get("PORT", "8765")))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"开放获客工作台 running: http://{host}:{port}/index.html")
    print("Read-only mode: xhs status/search/read/comments only. Press Ctrl+C to stop.")
    server.serve_forever()
