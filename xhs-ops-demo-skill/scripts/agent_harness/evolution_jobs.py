"""Local background reflection and exclusive model/configuration reservations."""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable

from workbench_store import WorkbenchStore, digest
from .offering import SERVICE_POLICY_VERSION
from .runtime import redact_external


class ModelJobBusy(RuntimeError):
    pass


class ModelJobGate:
    """Reserve under a short lock; a worker may release or hand off the token."""
    def __init__(self):
        self.lock = threading.RLock()
        self.active: dict[str, Any] | None = None

    def reserve(self, kind: str, *, task_id: int | None = None, inherited_token: str | None = None) -> str:
        with self.lock:
            if self.active and self.active["token"] != inherited_token:
                raise ModelJobBusy("已有模型作业正在运行，请完成后再启动任务、修改设置或切换 Prompt。")
            if inherited_token and (not self.active or self.active["token"] != inherited_token):
                raise ModelJobBusy("模型作业预约已经失效。")
            token = uuid.uuid4().hex
            self.active = {"token": token, "kind": kind, "taskId": task_id}
            return token

    def release(self, token: str | None):
        with self.lock:
            if self.active and self.active["token"] == token:
                self.active = None


class EvolutionJobs:
    def __init__(self, storage, service_factory: Callable, gate: ModelJobGate,
                 *, safe_error: Callable[[Exception], str] | None = None):
        self.storage, self.service_factory, self.gate = storage, service_factory, gate
        self.safe_error = safe_error or (lambda exc: str(redact_external(str(exc))))
        self.lock = threading.RLock()
        self.threads: dict[int, threading.Thread] = {}

    def recover_interrupted(self):
        # Only persisted reservations are recovered. Historical finished tasks are not scheduled.
        with self.storage.connect() as db:
            rows = db.execute("SELECT task_id,payload FROM task_summaries").fetchall()
        for row in rows:
            try:
                saved = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(saved, dict):
                continue
            job = saved.get("evolutionJob") or {}
            if job.get("status") == "running":
                saved["evolutionJob"] = {**job, "status": "interrupted", "finishedAt": time.time(),
                    "error": "复盘进程已中断，未自动重发；请确认后显式重试。"}
                self.storage.save_task_summary(row["task_id"], saved)

    def eligible(self, task: dict, *, automatic=False) -> bool:
        if task.get("status") not in {"completed", "partial"}:
            return False
        result = task.get("result") or {}
        container = result.get("leads") or {}
        leads = container.get("leads") if isinstance(container, dict) else container
        if not leads and not result.get("report"):
            return False
        for run in self.storage.list_agent_runs(task["id"], limit=100000):
            if run.get("agentKey") not in {"relevance_agent", "lead_signal_agent", "critic_qa_agent", "report_agent"}:
                continue
            if run.get("runType") not in {"main", "batch", ""}:
                continue
            if not automatic:
                return True
            # A stored mock trace is not proof of a real request; automatic
            # reflection requires evidence of an actual model call.
            if run.get("hasFullAudit"):
                call = self.storage.get_agent_call(run["callId"], task_id=task["id"])
                if call and (call.get("metrics", {}).get("usage", {}).get("requestCount") or 0) > 0:
                    return True
        return False

    def start(self, task_id: int, *, retry=False, automatic=False, inherited_token: str | None = None,
              sales_user_id: int | None = 0) -> dict:
        with self.lock:
            task = self.storage.get_task(task_id, sales_user_id=sales_user_id)
            if not task:
                raise ValueError("任务不存在。")
            state = self.status(task_id)
            if state["isEvolving"]:
                return state
            previous = state["evolution"]["status"]
            if previous != "not_started":
                if automatic:
                    return state
                changed_evidence = False
                old_hash = state["evolution"].get("evidenceHash")
                if old_hash and previous != "interrupted":
                    service = self.service_factory()
                    if hasattr(service, "_task_context"):
                        changed_evidence = digest(service._task_context(task)) != old_hash
                if not changed_evidence and not (retry and previous in {"failed", "interrupted"}):
                    return state
            if not self.eligible(task, automatic=automatic):
                if automatic:
                    return state
                raise ValueError("任务须已完成或部分完成，且有实际 Agent 记录及候选或报告，才能复盘。")
            token = self.gate.reserve("evolution", task_id=task_id, inherited_token=inherited_token)
            try:
                saved = self.storage.get_task_summary(task_id) or {}
                saved["evolutionJob"] = {"status": "running", "jobId": uuid.uuid4().hex,
                    "startedAt": time.time(), "automatic": bool(automatic)}
                self.storage.save_task_summary(task_id, saved)
                thread = threading.Thread(target=self._run, args=(task_id, token, bool(retry)), daemon=True)
                self.threads[task_id] = thread
                thread.start()
            except Exception as exc:
                self.threads.pop(task_id, None)
                self.gate.release(token)
                self._finish_job(task_id, error=self.safe_error(exc))
                raise
            # Return the accepted state, even if a synthetic/very fast service just finished.
            return {"taskId": task_id, "isEvolving": True,
                    "evolution": {**state["evolution"], "status": "running", "job": saved["evolutionJob"]}}

    def _run(self, task_id: int, token: str, retry: bool):
        error = ""
        try:
            self.service_factory().reflect_task(task_id, auto_save_candidates=True, retry=retry)
        except Exception as exc:
            error = self.safe_error(exc)
        finally:
            try:
                self._finish_job(task_id, error=error)
            finally:
                self.gate.release(token)
                with self.lock:
                    self.threads.pop(task_id, None)

    def _finish_job(self, task_id, *, error=""):
        saved = self.storage.get_task_summary(task_id) or {}
        saved["evolutionJob"] = {**(saved.get("evolutionJob") or {}), "status": "finished",
                                 "finishedAt": time.time()}
        if error:
            saved["evolution"] = {**(saved.get("evolution") or {}), "status": "failed",
                "error": error, "summary": "复盘未完成；检索结果保持不变，可显式重试。"}
        elif (saved.get("evolution") or {}).get("error"):
            saved["evolution"]["error"] = self.safe_error(RuntimeError(str(saved["evolution"]["error"])))
        self.storage.save_task_summary(task_id, saved)

    def status(self, task_id: int) -> dict:
        saved = self.storage.get_task_summary(task_id) or {}
        evolution = dict(saved.get("evolution") or {"status": "not_started", "candidatePromptIds": []})
        job = saved.get("evolutionJob") or {}
        if job.get("status") in {"running", "interrupted"}:
            evolution["status"] = job["status"]
            if job.get("error"):
                evolution["error"] = job["error"]
        ids = set(evolution.get("candidatePromptIds") or [])
        with self.storage.connect() as db:
            rows = db.execute("SELECT candidate_prompt_id FROM lead_feedback WHERE task_id=? AND candidate_prompt_id IS NOT NULL", (task_id,)).fetchall()
        ids.update(row[0] for row in rows)
        ids.update(prompt["id"] for prompt in self.storage.list_prompt_versions()
                   if prompt.get("sourceTaskId") == task_id)
        evolution["candidateStates"] = self.candidate_states(ids)
        evolution["job"] = job
        return {"taskId": task_id, "evolution": evolution, "isEvolving": evolution["status"] == "running"}

    def candidate_states(self, ids) -> list[dict]:
        if not ids:
            return []
        WorkbenchStore(self.storage)  # Ensures only this storage's benchmark schema exists.
        candidates = [p for p in self.storage.list_prompt_versions() if p["id"] in ids]
        states = []
        for candidate in candidates:
            with self.storage.connect() as db:
                rows = db.execute("""SELECT payload FROM benchmark_runs WHERE prompt_id=? AND prompt_hash=?
                    ORDER BY created DESC""", (candidate["id"], digest(candidate["prompt"]))).fetchall()
            context = (candidate.get("metrics") or {}).get("learningContext") or {}
            matching = []
            for row in rows:
                run = json.loads(row["payload"])
                if run.get("split") != "acceptance":
                    continue
                if run.get("agentKey") and run["agentKey"] != candidate["agentKey"]:
                    continue
                if candidate["agentKey"] in {"critic_qa_agent", "report_agent"} and run.get("agentKey") != candidate["agentKey"]:
                    continue
                if run.get("servicePolicyVersion") and run["servicePolicyVersion"] != SERVICE_POLICY_VERSION:
                    continue
                if context.get("offeringId") and set(run.get("offeringCounts") or {}) != {context["offeringId"]}:
                    continue
                if context.get("offeringId") == "agent_custom_development" and run.get("servicePolicyVersion") != SERVICE_POLICY_VERSION:
                    continue
                matching.append(run)
            proof = matching[0] if matching else {}
            state = "not_evaluated"
            if proof:
                quality = candidate["agentKey"] not in {"critic_qa_agent", "report_agent"} or proof.get("qualityReviewComplete")
                if proof.get("passed") is True and proof.get("humanReviewComplete") is True and quality:
                    state = "passed"
                elif proof.get("autoPassed") is not True or (proof.get("humanReviewComplete") is True and quality):
                    state = "failed"
                else:
                    state = "pending_human_review"
            states.append({"promptId": candidate["id"], "status": candidate["status"], "evaluationState": state})
        return sorted(states, key=lambda item: item["promptId"])

    def annotate_feedback(self, result: dict) -> dict:
        result = dict(result)
        feedback = result.get("feedback") if isinstance(result.get("feedback"), dict) else result
        ids = [value for value in (result.get("candidatePromptId"), feedback.get("candidatePromptId")) if value]
        result["candidateStates"] = self.candidate_states(ids)
        if feedback.get("taskId"):
            result.update(self.status(int(feedback["taskId"])))
        return result
