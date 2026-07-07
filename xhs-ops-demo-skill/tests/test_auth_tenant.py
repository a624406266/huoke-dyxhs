from __future__ import annotations

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
        self.old_env = {key: os.environ.get(key) for key in ("XHS_DB_PATH", "XHS_ADMIN_PASSWORD_HASH", "SESSION_TTL_SECONDS")}
        self.addCleanup(self.restore_env)
        os.environ["XHS_DB_PATH"] = str(Path(self.tmp.name) / "server.db")
        os.environ["XHS_ADMIN_PASSWORD_HASH"] = hash_password("admin-secret")
        os.environ["SESSION_TTL_SECONDS"] = "300"
        import server

        self.server = importlib.reload(server)

        class QuietHandler(self.server.Handler):
            def log_message(self, format: str, *args: Any) -> None:
                return None

        self.httpd = self.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown_server)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def restore_env(self) -> None:
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

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
        status, body = self.request("POST", "/api/xhs_login/start", {"mode": "browser"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["mode"], "browser")

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

    def test_local_task_routes_use_default_local_user_without_login(self) -> None:
        local_task = self.server.STORAGE.create_task({"keyword": "local"}, sales_user_id=0)
        other_task = self.server.STORAGE.create_task({"keyword": "other"}, sales_user_id=123)

        status, body = self.request("POST", "/api/tasks/list", {"limit": 10})
        self.assertEqual(status, 200, body)
        self.assertEqual([task["id"] for task in body["data"]["tasks"]], [local_task])

        status, body = self.request("POST", "/api/tasks/status", {"taskId": other_task})
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
