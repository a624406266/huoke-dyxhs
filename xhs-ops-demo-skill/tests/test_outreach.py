from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from outreach import OutreachService, normalize_payload


def verified_result(action):
    return {"status": "succeeded", "submitted": True, "error": "", "evidence": {
        "verified": True, "method": "fixture-platform-receipt", "observedAt": time.time(),
        **{field: action[field] for field in ("accountIdentity", "targetUserId", "noteId", "commentId", "text")},
    }}


class FakeAdapter:
    def __init__(self):
        self.calls = []
        self.result = verified_result

    def execute(self, action):
        self.calls.append(action)
        return self.result(action)


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "actions.db"
        self.identity = {"identity": "xhs:account-a", "name": "Account A"}
        self.adapter = FakeAdapter()
        self.service = OutreachService(self.path, lambda platform: self.identity, lambda platform: self.adapter)

    def prepare(self, action="send_dm", **changes):
        payload = {"platform": "xhs", "action": action, "targetUserId": "target-1", "text": "你好" if action != "follow" else ""}
        payload.update(changes)
        return self.service.prepare(payload)

    def execute(self, action):
        return self.service.execute(action["id"], action["confirmationToken"])

    def test_prepare_is_preview_only_and_token_is_not_stored_or_listed(self):
        action = self.prepare()
        self.assertEqual(action["status"], "prepared")
        self.assertEqual(action["accountIdentity"], "xhs:account-a")
        self.assertIsNone(action["confirmedAt"])
        self.assertEqual(action["executionCount"], 0)
        self.assertFalse(self.adapter.calls)
        self.assertNotIn("confirmationToken", self.service.get(action["id"]))
        self.assertNotIn("confirmationToken", self.service.list()[0])
        self.assertNotIn(action["confirmationToken"].encode(), self.path.read_bytes())

    def test_verified_success_and_repeated_confirmation_submits_once(self):
        action = self.prepare()
        first = self.execute(action)
        self.assertEqual(first["status"], "succeeded")
        self.assertIsNotNone(first["confirmedAt"])
        self.assertEqual(first["executionCount"], 1)
        repeated = self.execute(action)
        self.assertEqual(repeated["status"], "succeeded")
        self.assertEqual(repeated["confirmedAt"], first["confirmedAt"])
        self.assertEqual(repeated["executionCount"], 1)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_confirmation_and_attempt_are_persisted_before_adapter_runs(self):
        action = self.prepare()
        def result(value):
            current = self.service.get(action["id"])
            self.assertEqual(current["status"], "executing")
            self.assertEqual(current["executionCount"], 1)
            self.assertIsNotNone(current["confirmedAt"])
            self.assertEqual(value["confirmedAt"], current["confirmedAt"])
            return verified_result(value)
        self.adapter.result = result
        self.assertEqual(self.execute(action)["status"], "succeeded")

    def test_invalid_token_never_claims_or_submits(self):
        action = self.prepare()
        with self.assertRaises(ValueError):
            self.service.execute(action["id"], "wrong")
        self.assertEqual(self.service.get(action["id"])["status"], "prepared")
        self.assertFalse(self.adapter.calls)

    def test_token_binds_text_target_account_and_expiry(self):
        for field, replacement in (("text", "changed"), ("targetUserId", "different-target")):
            action = self.prepare()
            with sqlite3.connect(self.path) as conn:
                payload = json.loads(conn.execute("SELECT payload_json FROM outreach_actions WHERE id=?", (action["id"],)).fetchone()[0])
                payload[field] = replacement
                conn.execute("UPDATE outreach_actions SET payload_json=? WHERE id=?", (json.dumps(payload), action["id"]))
            with self.assertRaises(ValueError):
                self.execute(action)
        action = self.prepare()
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE outreach_actions SET account_json=? WHERE id=?", ('{"identity":"xhs:other","name":""}', action["id"]))
        with self.assertRaises(ValueError):
            self.execute(action)
        self.assertFalse(self.adapter.calls)

    def test_account_switch_fails_before_adapter(self):
        action = self.prepare()
        self.identity = {"identity": "xhs:account-b"}
        result = self.execute(action)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])
        self.assertFalse(self.adapter.calls)

    def test_nickname_change_does_not_change_stable_identity(self):
        action = self.prepare()
        self.identity["name"] = "Renamed account"
        self.assertEqual(self.execute(action)["status"], "succeeded")

    def test_cannot_prepare_with_login_boolean_or_nickname_only(self):
        self.identity = {"ok": True, "nickname": "Alice"}
        with self.assertRaises(ValueError):
            self.prepare()

    def test_unverified_success_becomes_unknown_and_is_never_retried(self):
        self.adapter.result = lambda action: {"status": "succeeded", "submitted": True, "evidence": {"clicked": True}}
        action = self.prepare()
        self.assertEqual(self.execute(action)["status"], "unknown")
        self.execute(action)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_receipt_for_another_target_or_text_is_not_success(self):
        for field in ("targetUserId", "accountIdentity", "text"):
            def result(action, field=field):
                value = verified_result(action)
                value["evidence"][field] = "wrong"
                return value
            self.adapter.result = result
            self.assertEqual(self.execute(self.prepare(targetUserId="target-" + field))["status"], "unknown")

    def test_pre_submit_failure_and_post_submit_failure_are_distinct(self):
        self.adapter.result = lambda action: {"status": "failed", "submitted": False, "error": "No editor"}
        result = self.execute(self.prepare())
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])
        self.adapter.result = lambda action: {"status": "failed", "submitted": True, "error": "Lost receipt"}
        self.assertEqual(self.execute(self.prepare())["status"], "unknown")

    def test_exception_is_unknown_not_safe_to_retry(self):
        def crash(action):
            raise TimeoutError("request may have arrived")
        self.adapter.result = crash
        action = self.prepare()
        self.assertEqual(self.execute(action)["status"], "unknown")
        self.execute(action)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_atomic_claim_under_concurrent_requests(self):
        started, release = threading.Event(), threading.Event()
        def waiting(action):
            started.set()
            release.wait(5)
            return verified_result(action)
        self.adapter.result = waiting
        action = self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, action)
            self.assertTrue(started.wait(2))
            second = pool.submit(self.execute, action)
            self.assertEqual(second.result(timeout=2)["status"], "executing")
            release.set()
            self.assertEqual(first.result(timeout=2)["status"], "succeeded")
        self.assertEqual(len(self.adapter.calls), 1)

    def test_restart_marks_orphan_executing_unknown_without_resubmit(self):
        action = self.prepare()
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE outreach_actions SET status='executing', execution_owner=NULL WHERE id=?", (action["id"],))
        restarted = OutreachService(self.path, lambda platform: self.identity, lambda platform: self.adapter)
        self.assertEqual(restarted.get(action["id"])["status"], "unknown")
        self.assertEqual(restarted.execute(action["id"], action["confirmationToken"])["status"], "unknown")
        self.assertFalse(self.adapter.calls)

    def test_second_instance_does_not_interrupt_running_request(self):
        action = self.prepare()
        def result(value):
            other = OutreachService(self.path, lambda platform: self.identity, lambda platform: self.adapter)
            self.assertEqual(other.get(action["id"])["status"], "executing")
            return verified_result(value)
        self.adapter.result = result
        self.assertEqual(self.execute(action)["status"], "succeeded")

    def test_expired_confirmation_fails_without_sending(self):
        from unittest.mock import patch
        with patch("outreach.time.time", return_value=100):
            action = self.prepare()
        with patch("outreach.time.time", return_value=2000):
            result = self.execute(action)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["executionCount"], 0)
            self.assertIsNone(result["confirmedAt"])
        self.assertFalse(self.adapter.calls)

    def test_unknown_blocks_new_id_even_if_lead_or_url_changes(self):
        self.adapter.result = lambda value: {"status": "unknown", "submitted": True, "error": "lost receipt"}
        original = self.prepare(leadId="first")
        self.execute(original)
        before = self.service.list()
        with self.assertRaisesRegex(ValueError, "人工核实"):
            self.prepare(leadId="second", targetUrl="https://www.xiaohongshu.com/user/profile/target-1?source=changed", noteId="irrelevant-note")
        self.assertEqual(self.service.list(), before)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_executing_blocks_new_id_without_mutating_existing_action(self):
        entered, release = threading.Event(), threading.Event()
        def waiting(value):
            entered.set()
            release.wait(5)
            return verified_result(value)
        self.adapter.result = waiting
        original = self.prepare()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.execute, original)
            self.assertTrue(entered.wait(2))
            try:
                before = self.service.get(original["id"])
                with self.assertRaisesRegex(ValueError, "人工核实"):
                    self.prepare()
                self.assertEqual(self.service.get(original["id"]), before)
                self.assertEqual(len(self.service.list()), 1)
            finally:
                release.set()
            self.assertEqual(running.result(timeout=2)["status"], "succeeded")

    def test_two_previews_cannot_bypass_unresolved_action_guard(self):
        first, second = self.prepare(), self.prepare()
        self.adapter.result = lambda value: {"status": "unknown", "submitted": True, "error": "lost receipt"}
        self.execute(first)
        with self.assertRaisesRegex(ValueError, "人工核实"):
            self.execute(second)
        second_state = self.service.get(second["id"])
        self.assertEqual(second_state["status"], "prepared")
        self.assertIsNone(second_state["confirmedAt"])
        self.assertEqual(second_state["executionCount"], 0)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_success_allows_a_new_explicit_action(self):
        first = self.prepare()
        self.execute(first)
        second = self.prepare()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.execute(second)["status"], "succeeded")
        self.assertEqual(len(self.adapter.calls), 2)

    def test_unresolved_guard_is_scoped_to_account_target_action_and_text(self):
        self.adapter.result = lambda value: {"status": "unknown", "submitted": True, "error": "lost receipt"}
        self.execute(self.prepare())
        self.assertEqual(self.prepare(text="different")["status"], "prepared")
        self.assertEqual(self.prepare(targetUserId="target-2")["status"], "prepared")
        self.assertEqual(self.prepare(action="follow")["status"], "prepared")
        self.identity = {"identity": "xhs:account-b"}
        self.assertEqual(self.prepare()["status"], "prepared")

    def test_current_schema_reopen_preserves_confirmation_and_unresolved_action(self):
        self.adapter.result = lambda value: {"status": "unknown", "submitted": True, "error": "lost receipt"}
        first = self.execute(self.prepare())
        reopened = OutreachService(self.path, lambda platform: self.identity, lambda platform: self.adapter)
        record = reopened.get(first["id"])
        self.assertEqual(record, self.service.get(first["id"]))
        self.assertEqual(record["executionCount"], 1)
        self.assertIsNotNone(record["confirmedAt"])
        with self.assertRaisesRegex(ValueError, "人工核实"):
            reopened.prepare({"platform": "xhs", "action": "send_dm", "targetUserId": "target-1", "text": "你好"})
        self.assertEqual(len(reopened.list()), 1)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_target_urls_and_reply_required_fields(self):
        for url in ("https://evil.test/user/profile/target-1", "https://www.xiaohongshu.com/user/profile/other", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                self.prepare(targetUrl=url)
        with self.assertRaises(ValueError):
            self.prepare(action="reply_comment", noteId="note-1")
        normalized = normalize_payload({"platform": "douyin", "action": "reply_comment", "targetUserId": "sec-target", "noteId": "douyin:video1", "commentId": "douyin:c1", "text": "你好"})
        self.assertEqual(normalized["commentId"], "c1")
        self.assertEqual(normalized["targetUrl"], "https://www.douyin.com/video/video1")


if __name__ == "__main__":
    unittest.main()
