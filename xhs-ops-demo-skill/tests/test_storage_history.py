"""Regression checks use synthetic records in temporary databases only."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from storage import SalesStorage, hash_password


class StorageHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fixture.db"
        self.storage = SalesStorage(self.path)

    def seed_task(self, content, owner=0):
        task = self.storage.create_task({"keyword": "fixture"}, sales_user_id=owner)
        note = {"id": "shared-note", "platform": "xhs", "title": content}
        lead = {"lead_id": "shared-lead", "platform": "xhs", "note_id": "shared-note",
                "comment_id": "shared-comment", "content": content, "evidence": content, "score": 80}
        self.storage.save_note(task, note)
        self.storage.save_comment(task, note["id"], {"id": "shared-comment", "content": content,
            "user_info": {"nickname": content, "user_id": "user-" + content}})
        self.storage.save_lead(task, lead)
        self.storage.finish_task(task, result={"notes": [note], "leads": {"leads": [lead]}})
        return task

    def test_recollection_preserves_counts_evidence_and_owner_boundary(self):
        old = self.seed_task("original")
        new = self.seed_task("updated")
        self.seed_task("other-owner", owner=123)
        for task_id in (old, new):
            task = self.storage.get_task(task_id, sales_user_id=0)
            self.assertEqual((task["notesCount"], task["leadsCount"]), (1, 1))
        self.assertEqual(self.storage.get_lead("shared-lead", 0, task_id=old)["content"], "original")
        self.assertEqual(self.storage.get_lead("shared-lead", 0)["content"], "updated")
        self.assertEqual(self.storage.get_comment("shared-comment", 0, task_id=old)["userId"], "user-original")
        self.assertEqual(self.storage.comment_identities(["shared-comment"], 0, task_id=old)["shared-comment"]["nickname"], "original")
        self.assertIsNone(self.storage.get_lead("shared-lead", 123, task_id=old))
        self.assertIsNone(self.storage.get_lead("shared-lead", 0, task_id=99999))
        self.storage.save_feedback("shared-lead", "historical review", 0, task_id=old)
        self.assertEqual(self.storage.get_lead("shared-lead", 0, task_id=old)["feedback"], "historical review")
        self.assertNotEqual(self.storage.get_lead("shared-lead", 0).get("feedback"), "historical review")

    def test_reopening_current_schema_preserves_task_snapshots_and_identities(self):
        old = self.seed_task("original")
        new = self.seed_task("updated")
        with self.storage.connect() as db:
            originals = [tuple(row) for row in db.execute("SELECT id,result_json FROM tasks ORDER BY id")]
            entities = [tuple(row) for row in db.execute("SELECT * FROM task_entities ORDER BY task_id,entity_type")]
        for _ in range(2):
            self.storage = SalesStorage(self.path)
            self.assertEqual(self.storage.get_lead("shared-lead", 0, task_id=old)["content"], "original")
            self.assertEqual(self.storage.get_task(old)["leadsCount"], 1)
            self.assertEqual(self.storage.get_comment("shared-comment", 0, task_id=old)["userId"], "user-original")
            with self.storage.connect() as db:
                self.assertEqual([tuple(row) for row in db.execute("SELECT id,result_json FROM tasks ORDER BY id")], originals)
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM task_entities ORDER BY task_id,entity_type")], entities)
                self.assertEqual(db.execute("SELECT task_id FROM leads WHERE lead_id='shared-lead'").fetchone()[0], new)

    def test_password_rotation_revokes_sessions_but_unchanged_password_does_not(self):
        initial_hash = hash_password("fixture-secret")
        admin = self.storage.ensure_admin_user("admin", initial_hash)
        token = self.storage.create_session(admin["id"])["token"]
        original_reset = admin["passwordResetAt"]
        self.storage.ensure_admin_user("admin", initial_hash)
        self.assertIsNotNone(self.storage.get_session(token))
        self.storage.ensure_admin_user("admin", hash_password("fixture-secret"), password="fixture-secret")
        self.assertIsNotNone(self.storage.get_session(token))
        self.assertEqual(self.storage.get_user(admin["id"])["passwordResetAt"], original_reset)
        self.storage.ensure_admin_user("admin", hash_password("rotated-fixture-secret"))
        self.assertIsNone(self.storage.get_session(token))
        self.assertIsNone(self.storage.authenticate_user("admin", "fixture-secret"))
        self.assertIsNotNone(self.storage.authenticate_user("admin", "rotated-fixture-secret"))
        new_token = self.storage.create_session(admin["id"])["token"]
        self.assertIsNotNone(self.storage.get_session(new_token))


if __name__ == "__main__":
    unittest.main()
