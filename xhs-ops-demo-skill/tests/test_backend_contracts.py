"""Boundary regressions use memory-only configuration and synthetic SQLite data."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config_loader
from agent_harness.runtime import redact_external
from json_utils import canonical_json, parse_json
from llm_client import LLMRequestError, call_llm
from storage import SalesStorage, hash_password, is_password_hash, verify_password
from workbench_store import WorkbenchStore


class SettingsBoundaryTests(unittest.TestCase):
    def save(self, payload):
        with patch.object(config_loader, "load_local_config", return_value={}), patch.object(
            config_loader, "save_local_config"
        ) as save, patch.object(config_loader, "load_config", return_value={}):
            config_loader.update_local_settings(payload)
        return save.call_args.args[0]

    def test_temperature_zero_is_preserved_in_saved_and_public_settings(self):
        config = self.save({"llm": {"temperature": 0}})
        self.assertEqual(config["llm"]["temperature"], 0)
        public = config_loader.public_settings(config)
        self.assertEqual(public["llm"]["temperature"], 0)
        json.dumps(public, allow_nan=False)

    def test_invalid_temperature_never_saves_or_calls_transport(self):
        for value in (None, "", True, False, -1, 2.01, "NaN", "Infinity", float("nan"), float("inf")):
            with self.subTest(value=value), patch.object(config_loader, "load_local_config", return_value={}), \
                    patch.object(config_loader, "save_local_config") as save:
                with self.assertRaisesRegex(ValueError, "temperature"):
                    config_loader.update_local_settings({"llm": {"temperature": value}})
                save.assert_not_called()
            if value is None:
                continue  # Omitted transport temperature uses the default; saved settings cannot be null.
            with self.subTest(transport=value), patch("llm_client._await_request") as request:
                with self.assertRaises(LLMRequestError) as caught:
                    call_llm([], api_base="http://127.0.0.1:1/v1", model="fixture", temperature=value)
                self.assertEqual(caught.exception.code, "configuration_error")
                request.assert_not_called()

    def test_platform_switches_require_booleans_and_preserve_false(self):
        for platform in ("xhs", "douyin"):
            for field in ("enable_follow_actions", "enable_dm_actions"):
                self.assertFalse(self.save({platform: {field: False}})[platform][field])
                for value in ("false", "true", 0, 1, None):
                    with self.subTest(platform=platform, field=field, value=value), \
                            patch.object(config_loader, "load_local_config", return_value={}), \
                            patch.object(config_loader, "save_local_config") as save:
                        with self.assertRaises(ValueError):
                            config_loader.update_local_settings({platform: {field: value}})
                        save.assert_not_called()

    def test_current_settings_do_not_offer_unused_pipeline_controls(self):
        for platform in ("xhs", "douyin"):
            expected = {"enable_follow_actions", "enable_dm_actions"} | ({"bin"} if platform == "xhs" else set())
            self.assertEqual(set(config_loader.DEFAULT_CONFIG[platform]), expected)
            self.assertEqual(set(config_loader.public_settings(config_loader.DEFAULT_CONFIG)[platform]), expected)


class CredentialBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = SalesStorage(Path(directory.name) / "fixture.db")

    def test_malformed_hash_cannot_replace_an_account_or_revoke_sessions(self):
        good = hash_password("fixture-password")
        admin = self.storage.ensure_admin_user("admin", good)
        token = self.storage.create_session(admin["id"])["token"]
        salt, digest = "a" * 32, "b" * 64
        invalid = ["pbkdf2_sha256$broken", f"pbkdf2_sha256$0${salt}${digest}",
                   f"pbkdf2_sha256$1${salt}${digest}", f"pbkdf2_sha256$999999999${salt}${digest}",
                   f"pbkdf2_sha256$260000$bad${digest}", f"pbkdf2_sha256$260000${salt}$not-hex",
                   f"pbkdf2_sha256$260000${salt}${digest}$extra", None]
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(is_password_hash(value))
                with patch("storage.hashlib.pbkdf2_hmac") as derive:
                    self.assertFalse(verify_password("fixture-password", value))
                    derive.assert_not_called()
                with self.assertRaises(ValueError):
                    self.storage.ensure_admin_user("admin", value)
                with self.assertRaises(ValueError):
                    self.storage.reset_user_password(admin["id"], value)
                if value is not None:
                    with self.assertRaises(ValueError):
                        self.storage.upsert_user(username="admin", role="admin", password_hash=value)
                self.assertEqual(self.storage.get_user(admin["id"]), admin)
                self.assertIsNotNone(self.storage.get_session(token))
        self.assertIsNotNone(self.storage.authenticate_user("admin", "fixture-password"))

    def test_cookie_headers_are_redacted_in_full_before_persisting_events(self):
        task = self.storage.create_task({"keyword": "fixture"})
        values = ["HTTP failure Cookie: sid=fixture1; a1=fixture2; web_session=fixture3",
                  'headers={"Cookie": "sid=fixture1; a1=fixture2; web_session=fixture3"}',
                  "Cookie: sid=fixture1;\r\n a1=fixture2; web_session=fixture3\r\nStatus: 403",
                  "Set-Cookie: sid=fixture1; a1=fixture2; web_session=fixture3"]
        for value in values:
            sanitized = redact_external(value)
            self.storage.add_task_event(task, "warning", "fixture", "读取失败", {"error": sanitized})
        events = self.storage.get_task(task)["events"]
        serialized = json.dumps(events)
        for secret in ("fixture1", "fixture2", "fixture3"):
            self.assertNotIn(secret, serialized)
        self.assertIn("Status: 403", events[2]["data"]["error"])
        structured = redact_external({"headers": {"Set-Cookie": ["sid=fixture1", "sid=fixture2"],
                                                    "Proxy-Authorization": "fixture3"}})
        self.assertNotIn("fixture", json.dumps(structured))
        serialized = redact_external(json.dumps({"Cookie": "a=fixture1; b=fixture2", "source": "preserve source"}))
        self.assertEqual(json.loads(serialized), {"Cookie": "[已脱敏]", "source": "preserve source"})


class StorageReadBudgetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = SalesStorage(Path(directory.name) / "fixture.db")
        self.store = WorkbenchStore(self.storage)

    def test_annotation_uses_one_connection_and_bounded_batches(self):
        leads = [{"lead_id": f"lead-{i}", "content": f"source-{i}"} for i in range(850)]
        plan = {"keywords": ["fixture"]}
        with self.storage.connect() as db:
            for lead in leads:
                db.execute("INSERT INTO human_reviews(lead_id,basis,task_id,status,note,actor,created) VALUES(?,?,1,'approved','verified',1,1)",
                           (lead["lead_id"], self.store.review_basis(lead, plan)))
            db.execute("INSERT INTO human_reviews(lead_id,basis,task_id,status,note,actor,created) VALUES(?,?,2,'rejected','updated',1,2)",
                       (leads[0]["lead_id"], self.store.review_basis(leads[0], plan)))
        queries = []
        original = self.storage.connect

        @contextmanager
        def trace():
            with original() as db:
                db.set_trace_callback(queries.append)
                yield db

        with patch.object(self.storage, "connect", side_effect=trace) as connect:
            result = self.store.annotate({"leads": {"leads": copy.deepcopy(leads)}}, plan)
            self.assertEqual(connect.call_count, 1)
        self.assertEqual(len([q for q in queries if q.startswith("WITH requested")]), 3)
        self.assertEqual(result["leads"]["leads"][0]["review"]["status"], "rejected")
        self.assertTrue(all(lead["review"]["status"] == "approved" for lead in result["leads"]["leads"][1:]))
        changed = self.store.annotate({"leads": {"leads": [dict(leads[0], content="changed")]}}, plan)
        self.assertEqual(changed["leads"]["leads"][0]["review"]["status"], "pending")

    def test_task_list_does_not_read_full_result_documents(self):
        task = self.storage.create_task({"keyword": "fixture"}, sales_user_id=9)
        self.storage.finish_task(task, result={"report": "x" * 1000000})
        original = self.storage.connect

        @contextmanager
        def deny_results():
            with original() as db:
                db.set_authorizer(lambda action, table, column, *_:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "tasks" and column == "result_json"
                    else sqlite3.SQLITE_OK)
                yield db

        with patch.object(self.storage, "connect", side_effect=deny_results):
            self.assertEqual([row["id"] for row in self.storage.list_tasks(sales_user_id=9)], [task])
            self.assertEqual(self.storage.list_tasks(sales_user_id=8), [])

    def test_json_helpers_keep_canonical_hashes_and_reject_nonfinite_values(self):
        self.assertEqual(canonical_json({"b": 2, "a": "中文"}), '{"a":"中文","b":2}')
        self.assertEqual(parse_json('"ok"', {}), "ok")
        for value in ("{", "NaN", "Infinity", "", None):
            self.assertEqual(parse_json(value, {"fallback": True}), {"fallback": True})
        with self.assertRaises(ValueError):
            canonical_json({"temperature": float("nan")})


if __name__ == "__main__":
    unittest.main()
