"""Background learning never contacts a provider or opens the real database."""
from __future__ import annotations

import importlib.util
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from agent_harness.evolution_jobs import EvolutionJobs, ModelJobGate, ModelJobBusy
from agent_harness.registry import PromptRegistry
from storage import SalesStorage
from workbench_store import WorkbenchStore, digest
from config_loader import DEFAULT_CONFIG


class EvolutionJobsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fixture.db"
        self.storage = SalesStorage(self.path)
        self.gate = ModelJobGate()

    def task(self, *, actual=False, owner=0, status="completed"):
        task = self.storage.create_task({"keyword": "synthetic"}, sales_user_id=owner)
        self.storage.save_agent_run(task_id=task, agent_key="lead_signal_agent", agent_name="fixture",
            prompt_version="fixture", model="fixture", purpose="", input_summary="{}", output_summary="{}",
            confidence=0.8, error="", duration_ms=1, call_id=f"call-{task}" if actual else "")
        if actual:
            self.storage.start_agent_call(call_id=f"call-{task}", task_id=task, agent_key="lead_signal_agent",
                input_payload={}, request={}, metrics={}, started_at=time.time())
            self.storage.finish_agent_call(f"call-{task}", status="completed", output={},
                metrics={"usage": {"requestCount": 1}})
        self.storage.finish_task(task, status=status, result={"status": status, "report": {"summary": "synthetic report"}})
        return task

    def service(self, *, release=None, fail=False):
        storage, calls = self.storage, []
        class Fake:
            def reflect_task(self, task_id, **kwargs):
                calls.append((task_id, kwargs))
                if release is not None:
                    release.wait(3)
                if fail:
                    raise ValueError("synthetic failure")
                saved = storage.get_task_summary(task_id)
                saved["evolution"] = {"status": "needs_review", "summary": "synthetic reflection", "candidatePromptIds": []}
                storage.save_task_summary(task_id, saved)
        return Fake(), calls

    def wait(self, jobs, task):
        thread = jobs.threads.get(task)
        if thread:
            thread.join(4)
            self.assertFalse(thread.is_alive())

    def test_same_task_double_click_is_idempotent_and_gate_blocks_other_work(self):
        task = self.task()
        release = threading.Event()
        self.addCleanup(release.set)
        service, calls = self.service(release=release)
        jobs = EvolutionJobs(self.storage, lambda: service, self.gate)
        self.assertTrue(jobs.start(task)["isEvolving"])
        self.assertTrue(jobs.start(task, retry=True)["isEvolving"])
        with self.assertRaises(ModelJobBusy):
            self.gate.reserve("settings")
        release.set()
        self.wait(jobs, task)
        self.assertEqual(len(calls), 1)
        self.assertEqual(jobs.status(task)["evolution"]["status"], "needs_review")
        self.assertFalse(jobs.start(task)["isEvolving"])
        self.assertEqual(len(calls), 1)
        self.assertIsNone(self.gate.active)

    def test_failed_reflection_preserves_finished_task_and_requires_explicit_retry(self):
        task = self.task()
        before = self.storage.get_task(task)
        service, calls = self.service(fail=True)
        jobs = EvolutionJobs(self.storage, lambda: service, self.gate)
        jobs.start(task)
        self.wait(jobs, task)
        self.assertEqual(jobs.status(task)["evolution"]["status"], "failed")
        jobs.start(task)
        self.assertEqual(len(calls), 1)
        jobs.start(task, retry=True)
        self.wait(jobs, task)
        self.assertEqual(len(calls), 2)
        after = self.storage.get_task(task)
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(after["result"], before["result"])
        self.assertEqual(after["finishedAt"], before["finishedAt"])

    def test_restart_recovers_only_unfinished_job_without_replaying_history(self):
        task = self.task()
        other = self.task()
        self.storage.save_task_summary(task, {"evolutionJob": {"status": "running", "jobId": "old"}})
        factory = Mock(side_effect=AssertionError("must not run"))
        jobs = EvolutionJobs(self.storage, factory, self.gate)
        jobs.recover_interrupted()
        self.assertEqual(jobs.status(task)["evolution"]["status"], "interrupted")
        self.assertEqual(jobs.status(other)["evolution"]["status"], "not_started")
        self.assertFalse(jobs.start(task)["isEvolving"])
        factory.assert_not_called()

    def test_automatic_reflection_requires_actual_request_and_handoff_keeps_gate_reserved(self):
        old = self.task()
        task = self.task(actual=True, status="partial")
        release = threading.Event()
        self.addCleanup(release.set)
        service, calls = self.service(release=release)
        jobs = EvolutionJobs(self.storage, lambda: service, self.gate)
        self.assertFalse(jobs.start(old, automatic=True)["isEvolving"])
        token = self.gate.reserve("scan")
        jobs.start(task, automatic=True, inherited_token=token)
        self.gate.release(token)
        with self.assertRaises(ModelJobBusy):
            self.gate.reserve("new scan")
        release.set()
        self.wait(jobs, task)
        self.assertEqual([x[0] for x in calls], [task])
        self.assertIsNone(self.gate.active)

    def test_scope_ownership_is_checked_before_start(self):
        task = self.task(owner=7)
        factory = Mock()
        jobs = EvolutionJobs(self.storage, factory, self.gate)
        with self.assertRaisesRegex(ValueError, "不存在"):
            jobs.start(task, sales_user_id=0)
        factory.assert_not_called()

    def test_reflection_thread_start_failure_is_visible_and_releases_reservation(self):
        task = self.task()
        jobs = EvolutionJobs(self.storage, Mock(), self.gate)
        with patch("agent_harness.evolution_jobs.threading.Thread.start", side_effect=RuntimeError("synthetic start failure")):
            with self.assertRaises(RuntimeError):
                jobs.start(task)
        self.assertIsNone(self.gate.active)
        self.assertNotIn(task, jobs.threads)
        self.assertEqual(jobs.status(task)["evolution"]["status"], "failed")

    def test_manual_new_evidence_reflects_again_but_unchanged_evidence_and_auto_are_idempotent(self):
        task = self.task(actual=True)
        storage, evidence, calls = self.storage, {"humanFeedback": []}, []
        class Fake:
            def _task_context(self, task):
                return evidence
            def reflect_task(self, task_id, **kwargs):
                calls.append(task_id)
                saved = storage.get_task_summary(task_id)
                saved["evolution"] = {"status": "needs_review", "candidatePromptIds": [],
                                      "evidenceHash": digest(evidence)}
                storage.save_task_summary(task_id, saved)
        jobs = EvolutionJobs(storage, Fake, self.gate)
        jobs.start(task, automatic=True)
        self.wait(jobs, task)
        self.assertFalse(jobs.start(task)["isEvolving"])
        evidence["humanFeedback"].append({"reason": "new independently recorded evidence"})
        self.assertFalse(jobs.start(task, automatic=True)["isEvolving"])
        self.assertTrue(jobs.start(task)["isEvolving"])
        self.wait(jobs, task)
        self.assertFalse(jobs.start(task)["isEvolving"])
        self.assertEqual(calls, [task, task])

    def test_candidate_states_require_matching_frozen_proof_and_do_not_expose_prompt(self):
        registry = PromptRegistry(self.storage)
        registry.ensure_defaults()
        wb = WorkbenchStore(self.storage)
        ids = []
        for index in range(5):
            ids.append(self.storage.create_prompt_candidate(agent_key="lead_signal_agent", agent_name="fixture",
                base_version="fixture", prompt=f"private-prompt-{index}", source_task_id=None, notes="fixture"))
        rows = {p["id"]: p for p in registry.prompt_versions()}
        for index, extra in ((1, {"autoPassed": False}), (2, {"autoPassed": True}),
                             (3, {"autoPassed": True, "passed": True, "humanReviewComplete": True})):
            wb.save_benchmark({"datasetId": "fixture", "promptId": ids[index], "promptHash": digest(rows[ids[index]]["prompt"]),
                "split": "acceptance", "agentKey": "lead_signal_agent", **extra})
        wb.save_benchmark({"datasetId": "fixture", "promptId": ids[4], "promptHash": digest("different prompt"),
            "split": "acceptance", "autoPassed": True, "passed": True, "humanReviewComplete": True})
        jobs = EvolutionJobs(self.storage, Mock(), self.gate)
        states = jobs.candidate_states(ids)
        self.assertEqual([x["evaluationState"] for x in states],
                         ["not_evaluated", "failed", "pending_human_review", "passed", "not_evaluated"])
        self.assertNotIn("private-prompt", json.dumps(states))


class EvolutionServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["storage"]["db_path"] = str(Path(directory.name) / "server.db")
        config["llm"]["model"] = "fixture"
        spec = importlib.util.spec_from_file_location("synthetic_evolution_server", SCRIPTS / "server.py")
        self.server = importlib.util.module_from_spec(spec)
        with patch("config_loader.load_config", side_effect=AssertionError("tests must not load local configuration")), \
                patch.dict(os.environ, {}, clear=True):
            spec.loader.exec_module(self.server)
            self.server.initialize_app(config)

    def handler(self, path, payload=None):
        handler = object.__new__(self.server.Handler)
        handler.path = path
        handler.local_request = lambda: True
        handler.require_admin = lambda: {"id": 1}
        handler.read_payload = lambda: payload or {}
        handler.current_session = lambda: {"user": {"id": 1}}
        handler.respond_json = Mock()
        return handler

    def test_busy_background_job_blocks_settings_new_task_and_prompt_changes(self):
        token = self.server.MODEL_JOBS.reserve("evolution")
        for path in ("/api/settings", "/api/prompts/promote", "/api/prompts/rollback", "/api/tasks/start", "/api/evals/run"):
            with self.subTest(path=path):
                handler = self.handler(path)
                handler.do_POST()
                self.assertEqual(handler.respond_json.call_args.kwargs["status"], 409)
                self.assertFalse(handler.respond_json.call_args.args[0]["ok"])
        self.server.MODEL_JOBS.release(token)

    def test_scan_thread_start_failure_does_not_leave_active_task_or_gate(self):
        with patch.object(self.server, "confirmed_task", return_value={}), \
             patch.object(self.server.threading.Thread, "start", side_effect=RuntimeError("synthetic start failure")):
            with self.assertRaises(RuntimeError):
                self.server.task_manager.start_task({})
        self.assertIsNone(self.server.MODEL_JOBS.active)
        self.assertEqual(self.server.task_manager.active_threads, {})
        self.assertEqual(self.server.STORAGE.list_tasks(1)[0]["status"], "failed")

    def test_prompt_promote_uses_verified_registry_path_and_rollback_passes_agent(self):
        registry = Mock()
        registry.promote_evaluated.return_value = {"status": "active"}
        registry.rollback.return_value = {"status": "active"}
        with patch.object(self.server, "get_registry", return_value=registry):
            self.handler("/api/prompts/promote", {"promptId": 10, "evalRunId": "proof"}).do_POST()
            self.handler("/api/prompts/rollback", {"promptId": 4, "agentKey": "lead_signal_agent"}).do_POST()
        registry.promote_evaluated.assert_called_once_with(10, "proof")
        registry.rollback.assert_called_once_with("lead_signal_agent", 4)
        registry.promote.assert_not_called()
        self.assertIsNone(self.server.MODEL_JOBS.active)

    def test_complete_audit_endpoint_enforces_admin_and_task_owner(self):
        storage = self.server.STORAGE
        task = storage.create_task({}, sales_user_id=7)
        storage.start_agent_call(call_id="private-call", task_id=task, agent_key="fixture", input_payload={}, request={}, metrics={}, started_at=time.time())
        handler = self.handler(f"/api/agents/call?taskId={task}&callId=private-call")
        handler._get()
        self.assertEqual(handler.respond_json.call_args.kwargs["status"], 404)
        handler = self.handler(f"/api/agents/call?taskId={task}&callId=private-call")
        handler.require_admin = lambda: None
        with patch.object(storage, "get_agent_call") as get:
            handler._get()
            get.assert_not_called()

    def test_task_status_exposes_summary_evolution_without_changing_execution_result(self):
        storage = self.server.STORAGE
        task = storage.create_task({})
        storage.finish_task(task, result={"status": "completed", "report": {"summary": "kept"}})
        storage.save_task_summary(task, {"evolution": {"status": "failed", "error": "synthetic"}})
        handler = self.handler(f"/api/tasks/status?taskId={task}")
        handler._get()
        status = handler.respond_json.call_args.args[0]["data"]
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["evolution"]["status"], "failed")
        self.assertFalse(status["isEvolving"])
        self.assertEqual(storage.get_task(task)["result"]["report"]["summary"], "kept")

    def test_task_end_saves_terminal_result_before_starting_background_reflection(self):
        storage = self.server.STORAGE
        task = storage.create_task({})
        observed = []
        def started(task_id, **kwargs):
            observed.append(storage.get_task(task_id)["status"])
            raise ValueError("synthetic reflection scheduling failure")
        manager = self.server.TaskManager()
        with patch.object(manager, "_run_single", return_value={"status": "partial", "report": {"summary": "kept"}}), \
             patch.object(self.server.evolution_jobs, "start", side_effect=started):
            manager._run_task(task, {}, "single", 0)
        self.assertEqual(observed, ["partial"])
        self.assertEqual(storage.get_task(task)["status"], "partial")


if __name__ == "__main__":
    unittest.main()
