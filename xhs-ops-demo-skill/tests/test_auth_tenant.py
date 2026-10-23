from __future__ import annotations

import copy
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from storage import SalesStorage, hash_password
from config_loader import DEFAULT_CONFIG


class StorageAuthTenantTests(unittest.TestCase):
    def make_storage(self) -> SalesStorage:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SalesStorage(Path(tmp.name) / "sales.db")

    def test_user_session_disable_and_password_reset(self) -> None:
        storage = self.make_storage()
        admin = storage.ensure_admin_user("admin", hash_password("admin-secret"))
        self.assertEqual(admin["role"], "admin")
        self.assertEqual(storage.authenticate_user("admin", "admin-secret", role="admin")["id"], admin["id"])

        sales = storage.upsert_sales_user(
            username="alice",
            display_name="Alice",
            password_hash=hash_password("old-password"),
        )
        self.assertEqual(storage.authenticate_user("alice", "old-password", role="sales")["id"], sales["id"])

        session = storage.create_session(sales["id"], ttl_seconds=300)
        self.assertEqual(storage.get_session(session["token"])["user"]["id"], sales["id"])
        storage.revoke_session(session["token"])
        self.assertIsNone(storage.get_session(session["token"]))

        storage.set_user_enabled(sales["id"], False)
        self.assertIsNone(storage.authenticate_user("alice", "old-password", role="sales"))
        storage.set_user_enabled(sales["id"], True)
        storage.reset_user_password(sales["id"], hash_password("new-password"))
        self.assertIsNone(storage.authenticate_user("alice", "old-password", role="sales"))
        self.assertEqual(storage.authenticate_user("alice", "new-password", role="sales")["id"], sales["id"])

    def test_sales_records_are_filtered_by_sales_user_id(self) -> None:
        storage = self.make_storage()
        alice = storage.upsert_sales_user(username="alice", password_hash=hash_password("alice-pass"))
        bob = storage.upsert_sales_user(username="bob", password_hash=hash_password("bob-pass"))
        alice_task = storage.create_task({"keyword": "腾讯云"}, sales_user_id=alice["id"])
        bob_task = storage.create_task({"keyword": "腾讯云"}, sales_user_id=bob["id"])

        storage.save_comment(alice_task, "n1", {"id": "c-shared", "content": "Alice 线索", "user_info": {"nickname": "Alice客户"}})
        storage.save_comment(bob_task, "n1", {"id": "c-shared", "content": "Bob 线索", "user_info": {"nickname": "Bob客户"}})
        lead = {
            "lead_id": "n1:c-shared",
            "note_id": "n1",
            "comment_id": "c-shared",
            "score": 90,
            "priority": "A",
            "demand_summary": "报价",
            "evidence": "怎么报价？",
            "outreach_copy": "人工跟进",
            "feedback": "待人工判断",
        }
        storage.save_lead(alice_task, {**lead, "demand_summary": "Alice 报价"})
        storage.save_lead(bob_task, {**lead, "demand_summary": "Bob 报价"})
        alice_feedback = storage.create_lead_feedback(
            task_id=alice_task,
            sales_user_id=alice["id"],
            lead_id="n1:c-shared",
            comment_id="c-shared",
            feedback_type="valid_lead",
            evidence="怎么报价？",
            expected_result="保持为线索",
        )
        bob_feedback = storage.create_lead_feedback(
            task_id=bob_task,
            sales_user_id=bob["id"],
            lead_id="n1:c-shared",
            comment_id="c-shared",
            feedback_type="invalid_lead",
            evidence="怎么报价？",
            expected_result="降低分数",
        )

        self.assertEqual([item["id"] for item in storage.list_tasks(sales_user_id=alice["id"])], [alice_task])
        self.assertIsNone(storage.get_task(bob_task, sales_user_id=alice["id"]))
        self.assertEqual(storage.get_lead("n1:c-shared", sales_user_id=alice["id"])["demand_summary"], "Alice 报价")
        self.assertEqual(storage.get_lead("n1:c-shared", sales_user_id=bob["id"])["demand_summary"], "Bob 报价")
        self.assertEqual(storage.comment_identities(["c-shared"], sales_user_id=alice["id"])["c-shared"]["nickname"], "Alice客户")
        self.assertEqual(storage.comment_identities(["c-shared"], sales_user_id=bob["id"])["c-shared"]["nickname"], "Bob客户")
        self.assertEqual([item["id"] for item in storage.list_lead_feedback(sales_user_id=alice["id"])], [alice_feedback])
        self.assertEqual([item["id"] for item in storage.list_lead_feedback(sales_user_id=bob["id"])], [bob_feedback])


class ServerAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"XHS_ADMIN_PASSWORD": ""})
        env.start()
        self.addCleanup(env.stop)
        import server

        self.server = importlib.reload(server)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["storage"]["db_path"] = str(Path(self.tmp.name) / "server.db")
        config["server"] = {"host": "127.0.0.1", "port": 0}
        config["auth"] = {"admin_username": "admin", "admin_password_hash": hash_password("admin-secret"), "session_ttl_seconds": 300}
        for platform in ("xhs", "douyin"):
            config[platform]["enable_follow_actions"] = False
            config[platform]["enable_dm_actions"] = False

        class QuietHandler(self.server.Handler):
            def log_message(self, format: str, *args: Any) -> None:
                return None

        self.httpd = self.server.WorkbenchHTTPServer(config, QuietHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown_server)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.token = self.login("admin", "admin-secret", "admin")

    def shutdown_server(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, token: str = "") -> tuple[int, dict[str, Any]]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def login(self, username: str, password: str, role: str) -> str:
        status, body = self.request("POST", "/api/auth/login", {"username": username, "password": password, "role": role})
        self.assertEqual(status, 200, body)
        return str(body["data"]["token"])

    def test_setup_status_reports_existing_admin(self) -> None:
        status, body = self.request("GET", "/api/setup/status")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["data"]["hasAdmin"])
        status, body = self.request("POST", "/api/setup/admin", {"username": "admin2", "password": "another-secret"})
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])

    def test_admin_bootstrap_preserves_unchanged_password_and_revokes_rotated_sessions(self):
        with patch.dict(self.server.CONFIG, {"auth": {"admin_username": "admin"}}), patch.dict(
            os.environ, {"XHS_ADMIN_PASSWORD": "admin-secret"},
        ):
            self.server.bootstrap_auth()
            self.server.bootstrap_auth()
        status, _ = self.request("GET", "/api/tasks/list", token=self.token)
        self.assertEqual(status, 200)
        with patch.dict(self.server.CONFIG, {"auth": {"admin_username": "admin",
            "admin_password_hash": hash_password("rotated-fixture-secret")}}):
            self.server.bootstrap_auth()
        status, _ = self.request("GET", "/api/tasks/list", token=self.token)
        self.assertEqual(status, 401)
        self.login("admin", "rotated-fixture-secret", "admin")

    def test_historical_outreach_uses_selected_task_evidence_and_identity(self):
        task_ids = []
        for content in ("original fixture", "updated fixture"):
            task_id = self.server.STORAGE.create_task({"keyword": "fixture"}, sales_user_id=0)
            lead = {"lead_id": "shared-history", "note_id": "shared-note", "comment_id": "shared-comment", "source_comment_id": "shared-comment",
                    "platform": "xhs", "content": content, "evidence": content, "score": 80}
            self.server.STORAGE.save_comment(task_id, "shared-note", {"id": "shared-comment", "content": content,
                "user_info": {"user_id": "user-" + content, "nickname": content}})
            self.server.STORAGE.save_lead(task_id, lead)
            self.server.STORAGE.finish_task(task_id, result={"leads": {"leads": [lead]}})
            task_ids.append(task_id)
        with patch.object(self.server, "call_llm_json", return_value=({"text": "fixture"}, "{}")) as call:
            status, body = self.request("POST", "/api/outreach/draft",
                {"leadId": "shared-history", "taskId": task_ids[0]}, token=self.token)
            self.assertEqual(status, 200, body)
            source = json.loads(call.call_args.args[0][1]["content"])["source"]
            self.assertEqual(source["content"], "original fixture")
            status, _ = self.request("POST", "/api/outreach/draft",
                {"leadId": "shared-history", "taskId": 999999}, token=self.token)
            self.assertEqual(status, 400)
            self.assertEqual(call.call_count, 1)
        with patch.object(self.server, "outreach_service") as service:
            service.return_value.prepare.side_effect = lambda payload: payload
            status, body = self.request("POST", "/api/outreach/prepare",
                {"leadId": "shared-history", "taskId": task_ids[0], "action": "reply_comment"}, token=self.token)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["data"]["targetUserId"], "user-original fixture")
        status, body = self.request("POST", "/api/tasks/status", {"taskId": task_ids[0]}, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["leads"]["leads"][0]["raw_user_id"], "user-original fixture")

    def test_qr_waiting_status_does_not_probe_xhs_cli(self) -> None:
        class FakeQrHelper:
            def status(self) -> dict[str, Any]:
                return {"mode": "qrcode", "status": "waiting", "message": "等待扫码。"}

        self.server.LOGIN_HELPERS[self.server.LOCAL_SALES_USER_ID] = FakeQrHelper()
        with patch.object(self.server, "get_adapter", side_effect=AssertionError("xhs status should not be called")):
            payload = self.server.xhs_login_status_payload(self.server.LOCAL_SALES_USER_ID)
        self.assertEqual(payload["helper"]["status"], "waiting")
        self.assertNotIn("xhs", payload)
        self.assertNotIn("xhsError", payload)

    def test_xhs_login_start_accepts_browser_mode(self) -> None:
        class FakeModeHelper:
            def start(self, mode: str | None = None) -> dict[str, Any]:
                return {"mode": mode or "qrcode", "status": "running"}

        self.server.LOGIN_HELPERS[self.server.LOCAL_SALES_USER_ID] = FakeModeHelper()
        status, body = self.request("POST", "/api/xhs_login/start", {"mode": "browser"}, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["mode"], "browser")

    def test_xhs_login_reset_clears_local_state(self) -> None:
        class FakeResetHelper:
            def reset(self) -> dict[str, Any]:
                return {"status": "cleared", "removed": ["cookie-dir"]}

        self.server.LOGIN_HELPERS[self.server.LOCAL_SALES_USER_ID] = FakeResetHelper()
        status, body = self.request("POST", "/api/xhs_login/reset", {}, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["status"], "cleared")

    def test_removed_routes_are_unavailable_and_execution_requires_confirmation(self):
        with patch.object(self.server, "action_adapter", side_effect=AssertionError("must not submit")):
            for path in ("/api/leads/follow", "/api/leads/send-dm", "/api/leads/dm-draft", "/api/search", "/api/batch_task/start"):
                status, body = self.request("POST", path, {"leadId": "any"}, token=self.token)
                self.assertEqual(status, 404, body)
            status, body = self.request("POST", "/api/outreach/execute", {"leadId": "any"}, token=self.token)
            self.assertEqual(status, 400, body)
            self.assertIn("逐条确认", body["error"])

    def test_reply_rejects_synthetic_source_id_and_ignores_target_overrides(self):
        task = self.server.STORAGE.create_task({"keyword": "fixture"}, sales_user_id=0)
        lead = {"lead_id": "source-fixture", "platform": "douyin", "note_id": "douyin:note",
                "comment_id": "local-id", "source_comment_id": "", "id_is_synthetic": True,
                "raw_user_id": "actual-author", "score": 80}
        self.server.STORAGE.save_lead(task, lead)
        with patch.object(self.server, "outreach_service") as service:
            payload = {"leadId": lead["lead_id"], "taskId": task, "action": "reply_comment",
                       "platform": "xhs", "targetUserId": "wrong-user", "commentId": "fake-id", "noteId": "wrong-note"}
            status, body = self.request("POST", "/api/outreach/prepare", payload, token=self.token)
            self.assertEqual(status, 400, body)
            service.assert_not_called()
            self.server.STORAGE.save_lead(task, {**lead, "source_comment_id": "actual-comment", "id_is_synthetic": False})
            service.return_value.prepare.side_effect = lambda value: value
            status, body = self.request("POST", "/api/outreach/prepare", payload, token=self.token)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["data"]["platform"], "douyin")
            self.assertEqual(body["data"]["noteId"], "douyin:note")
            self.assertEqual(body["data"]["commentId"], "actual-comment")
            self.assertEqual(body["data"]["targetUserId"], "actual-author")

    def test_prepare_rejects_other_owner_lead(self):
        task = self.server.STORAGE.create_task({"keyword": "other"}, sales_user_id=123)
        self.server.STORAGE.save_lead(task, {"lead_id": "other", "note_id": "n", "comment_id": "c", "score": 80})
        status, body = self.request("POST", "/api/outreach/prepare", {"leadId": "other", "platform": "xhs", "action": "follow"}, token=self.token)
        self.assertEqual(status, 400)
        self.assertIn("线索不存在", body["error"])

    def test_planning_does_not_collect_and_start_requires_preview(self):
        with patch.object(self.server, "get_adapter", side_effect=AssertionError("planning must not collect")), patch("agent_harness.planning.StructuredAgentRunner.run", side_effect=RuntimeError("offline fixture")):
            status, body = self.request("POST", "/api/search/plan", {"seedKeywords": ["图书"]}, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["data"]["planId"])
        self.assertEqual(body["data"]["plan"]["keywords"], ["图书"])
        for mode in ("single", "batch"):
            status, body = self.request("POST", "/api/tasks/start", {"keyword": "图书", "mode": mode}, token=self.token)
            self.assertEqual(status, 400, body)

    def test_cross_origin_requests_are_rejected(self):
        request = urllib.request.Request(self.base_url + "/api/tasks/list", headers={"Origin": "https://evil.example", "Authorization": "Bearer " + self.token})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_quality_gate_rejects_unmeasured_prompt_and_partial_review(self):
        registry = self.server.get_registry()
        prompt_id = registry.prompt_versions()[0]['id']
        status, body = self.request('POST', '/api/prompts/promote', {'promptId': prompt_id}, token=self.token)
        self.assertEqual(status, 400, body)
        self.assertIn('评测', body['error'])
        run = self.server.workbench().save_benchmark({'datasetId': 'unit-fixture', 'promptId': prompt_id,
              'promptHash': 'fixture', 'autoPassed': True, 'passed': False, 'rows': [{'id': 'sample'}]})
        status, body = self.request('POST', '/api/evals/review', {'runId': run['runId'], 'reviewedSampleIds': []}, token=self.token)
        self.assertEqual(status, 400, body)

    def test_review_history_task_query_filters_same_lead_without_changing_permissions(self):
        lead = {'lead_id': 'shared-history-lead', 'evidence': '隔离原文'}
        tasks = []
        for review_status in ['approved', 'rejected']:
            task_id = self.server.STORAGE.create_task({'confirmedPlan': {}}, sales_user_id=0)
            self.server.STORAGE.update_task(task_id, result={'leads': {'leads': [lead]}})
            self.server.workbench().review({'taskId': task_id, 'leadId': lead['lead_id'], 'status': review_status}, 1)
            tasks.append(task_id)
        route = f'/api/leads/review-history?leadId={lead["lead_id"]}&taskId={tasks[0]}'
        self.assertEqual(self.request('GET', route)[0], 401)
        sales = self.server.STORAGE.upsert_sales_user(username='history-sales', password_hash=hash_password('fixture-secret'))
        sales_token = self.server.STORAGE.create_session(sales['id'], ttl_seconds=300)['token']
        self.assertEqual(self.request('GET', route, token=sales_token)[0], 403)
        status, body = self.request('GET', route, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual([(item['task_id'], item['status']) for item in body['data']['history']], [(tasks[0], 'approved')])
        status, body = self.request('GET', route.replace(f'taskId={tasks[0]}', f'taskId={tasks[1]}'), token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual([(item['task_id'], item['status']) for item in body['data']['history']], [(tasks[1], 'rejected')])
        status, body = self.request('GET', route.replace(f'taskId={tasks[0]}', 'taskId=999999'), token=self.token)
        self.assertEqual(status, 200, body); self.assertEqual(body['data']['history'], [])
        status, body = self.request('GET', route.split('&taskId=')[0], token=self.token)
        self.assertEqual(status, 200, body); self.assertEqual(len(body['data']['history']), 2, 'omitting taskId returns all reviews for the same lead')
        for invalid in ['0', '-1', '1.5', 'invalid']:
            status, body = self.request('GET', route.replace(f'taskId={tasks[0]}', f'taskId={invalid}'), token=self.token)
            self.assertEqual(status, 400, body); self.assertNotIn('history', body)

    def test_evaluation_sample_read_is_admin_only_and_scoped_to_selected_dataset(self):
        sample = {
            "id": "review-source-a", "noteId": "fixture-note-a", "topic": "fixture-topic",
            "sourceUrl": "https://example.invalid/source-a", "collectedAt": "2026-10-21",
            "text": "隔离样本原文 A；不能出现在目录中", "split": "acceptance",
            "labels": {"relevant": True, "classification": "demand"}, "annotator": "unit-test",
        }
        first = self.server.workbench().import_dataset({"name": "选择的样本集", "samples": [sample]})
        second_sample = {**sample, "id": "review-source-b", "noteId": "fixture-note-b", "text": "隔离样本原文 B"}
        self.server.workbench().import_dataset({"name": "另一个样本集", "samples": [second_sample]})
        route = "/api/evals/datasets?datasetId=" + first["datasetId"]

        status, body = self.request("GET", route)
        self.assertEqual(status, 401, body)
        self.assertNotIn(sample["text"], json.dumps(body, ensure_ascii=False))
        sales = self.server.STORAGE.upsert_sales_user(username="dataset-sales", password_hash=hash_password("fixture-secret"))
        sales_token = self.server.STORAGE.create_session(sales["id"], ttl_seconds=300)["token"]
        status, body = self.request("GET", route, token=sales_token)
        self.assertEqual(status, 403, body)
        self.assertNotIn(sample["text"], json.dumps(body, ensure_ascii=False))

        status, body = self.request("GET", route, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"], {"datasetId": first["datasetId"], "samples": [sample]})
        status, body = self.request("GET", "/api/evals/datasets?datasetId=missing-fixture", token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"], {"datasetId": "missing-fixture", "samples": []})

        status, body = self.request("GET", "/api/evals/datasets", token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["data"]["datasets"]), 2)
        self.assertNotIn("samples", body["data"])
        self.assertNotIn(sample["text"], json.dumps(body, ensure_ascii=False))
        self.assertNotIn(second_sample["text"], json.dumps(body, ensure_ascii=False))

    def test_ai_outreach_draft_uses_configured_model_timeouts(self) -> None:
        task_id = self.server.STORAGE.create_task({"keyword": "fixture"}, sales_user_id=0)
        self.server.STORAGE.save_lead(task_id, {
            "lead_id": "timeout-draft-fixture", "note_id": "note-fixture",
            "comment_id": "comment-fixture", "score": 75, "content": "fixture content",
        })
        options = {"connect_timeout_s": 16, "read_timeout_s": 46, "total_timeout_s": 200}
        llm = {"api_key": "fixture-only-key", "api_base": "http://fixture.invalid/v1",
               "model": "fixture-model", **options}
        with patch.dict(self.server.CONFIG, {"llm": llm}), patch.object(
            self.server, "call_llm_json", return_value=({"text": "fixture draft"}, '{"text":"fixture draft"}'),
        ) as call:
            status, body = self.request(
                "POST", "/api/outreach/draft",
                {"leadId": "timeout-draft-fixture", "action": "comment", "instruction": "fixture"},
                token=self.token,
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["text"], "fixture draft")
        for key, expected in options.items():
            self.assertEqual(call.call_args.kwargs[key], expected)
        self.assertTrue(callable(call.call_args.kwargs["event_callback"]))
        self.assertEqual(call.call_args.kwargs["retries"], 2)

    def test_harness_routes_require_admin_session(self) -> None:
        status, body = self.request("GET", "/api/agents")
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])

        sales = self.server.STORAGE.upsert_sales_user(username="sales-a", password_hash=hash_password("sales-secret"))
        sales_token = self.server.STORAGE.create_session(sales["id"], ttl_seconds=300)["token"]
        status, _ = self.request("GET", "/api/agents", token=sales_token)
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/evals/run", {"taskId": 1}, token=sales_token)
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/auth/login", {"username": "sales-a", "password": "sales-secret", "role": "sales"})
        self.assertEqual(status, 400)

        admin_token = self.login("admin", "admin-secret", "admin")
        status, body = self.request("GET", "/api/agents", token=admin_token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        removed_sales_user_path = "/api/admin/" + "sales" + "-users"
        status, _ = self.request("POST", removed_sales_user_path, {"username": "created-sales"}, token=admin_token)
        self.assertEqual(status, 404)

    def test_local_task_routes_require_login_and_isolate_local_data(self) -> None:
        local_task = self.server.STORAGE.create_task({"keyword": "local"}, sales_user_id=0)
        other_task = self.server.STORAGE.create_task({"keyword": "other"}, sales_user_id=123)

        status, _ = self.request("POST", "/api/tasks/list", {"limit": 10})
        self.assertEqual(status, 401)
        status, body = self.request("POST", "/api/tasks/list", {"limit": 10}, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertEqual([task["id"] for task in body["data"]["tasks"]], [local_task])

        status, body = self.request("POST", "/api/tasks/status", {"taskId": other_task}, token=self.token)
        self.assertEqual(status, 200, body)
        self.assertFalse(body["data"]["ok"])

        status, _ = self.request("POST", "/api/admin/tasks/list", {"limit": 10})
        self.assertEqual(status, 401)
        status, _ = self.request("POST", "/api/admin/tasks/audit", {"taskId": local_task})
        self.assertEqual(status, 401)

        admin_token = self.login("admin", "admin-secret", "admin")
        status, body = self.request("POST", "/api/admin/tasks/list", {"limit": 10}, token=admin_token)
        self.assertEqual(status, 200, body)
        self.assertEqual({task["id"] for task in body["data"]["tasks"]}, {local_task, other_task})

        status, body = self.request("POST", "/api/admin/tasks/status", {"taskId": other_task}, token=admin_token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["data"]["ok"])
        self.assertEqual(body["data"]["taskId"], other_task)
        status, body = self.request("POST", "/api/admin/tasks/audit", {"taskId": other_task, "limit": 100}, token=admin_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["items"], [])


if __name__ == "__main__":
    unittest.main()
