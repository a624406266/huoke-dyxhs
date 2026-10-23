"""Source matching and usable business contacts, with no network or real accounts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agent_harness.pipeline import HarnessPipeline
from agent_harness.planning import normalize_plan
from agent_harness.runtime import StructuredAgentRunner, redact_external
from storage import SalesStorage
from test_pipeline_coverage import Adapter, Runner, note


# Synthetic, non-platform IDs retain an embedded 11-digit phone-pattern boundary.
SOURCE_IDS = ("synthetic-note-a13800000000b", "synthetic-note-c13900000000d")
CONTACT = "需要开发客服工具，联系13812345678或buyer@example.com"


class SourceContactIntegrityTests(unittest.TestCase):
    def test_nested_and_serialized_business_sources_are_unchanged(self):
        payload = {"notes": [{"note_id": identity, "desc": CONTACT,
                   "nickname": "合成客户", "user_id": "fixture-user",
                   "url": f"https://www.xiaohongshu.com/explore/{identity}"}
                   for identity in SOURCE_IDS],
                   "candidate_id": ":".join(SOURCE_IDS), "target_id": "note:" + SOURCE_IDS[0],
                   "douyin_id": "synthetic-douyin-a13800000000b", "phone_as_source_id": "13812345678"}
        self.assertEqual(redact_external(payload), payload)
        self.assertEqual(json.loads(redact_external(json.dumps(payload))), payload)

    def test_credentials_still_do_not_become_business_evidence(self):
        payload = {"content": CONTACT, "api_key": "fixture-api-secret",
                   "Cookie": "sid=fixture-cookie", "password": "fixture-password",
                   "headers": {"Authorization": "Bearer fixture-token"},
                   "profileUrl": "https://example.test/u/13812345678?token=fixture-url-secret"}
        safe = redact_external(payload)
        serialized = json.dumps(safe)
        for secret in ("fixture-api-secret", "fixture-cookie", "fixture-password", "fixture-token", "fixture-url-secret"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(safe["content"], CONTACT)
        self.assertIn("/13812345678?token=", safe["profileUrl"])

    def test_credential_aliases_are_removed_from_fields_text_and_serialized_json(self):
        aliases = ("x-api-key", "password", "passwd", "secret", "client_secret", "clientSecret",
                   "auth_token", "authToken", "session_token", "sessionId", "xsec_token")
        for alias in aliases:
            with self.subTest(alias=alias):
                payload = {alias: "fixture-secret", "content": CONTACT, "note_id": SOURCE_IDS[0]}
                safe = redact_external(payload)
                self.assertEqual(safe[alias], "[已脱敏]")
                self.assertEqual(safe["content"], CONTACT)
                self.assertEqual(safe["note_id"], SOURCE_IDS[0])
                self.assertEqual(json.loads(redact_external(json.dumps(payload))), safe)
                for separator in ("=", ": "):
                    text = f'{alias}{separator}"fixture-secret with spaces"，{CONTACT}'
                    self.assertNotIn("fixture-secret", redact_external(text))
                    self.assertTrue(redact_external(text).endswith(CONTACT))

    def test_urls_remove_credentials_without_rewriting_source_or_contact_fields(self):
        aliases = ("key", "api_key", "x-api-key", "password", "client_secret", "session_token", "authToken")
        for alias in aliases:
            with self.subTest(alias=alias):
                source = f"https://example.test/{SOURCE_IDS[0]}?note_id={SOURCE_IDS[1]}&email=buyer%40example.com&phone=13812345678"
                original = source.replace("https://", "https://fixture-user:fixture-password@") + f"&{alias}=fixture-secret#original-anchor"
                safe = redact_external(original + "，" + CONTACT)
                self.assertEqual(safe, source + f"&{alias}=[已脱敏]#original-anchor，" + CONTACT)
        encoded = "https://example.test/source?session%5Ftoken=fixture-secret&note_id=" + SOURCE_IDS[0]
        self.assertEqual(redact_external(encoded), encoded.replace("fixture-secret", "[已脱敏]"))
        fragment = "https://example.test/source#access_token=fixture-secret&note_id=" + SOURCE_IDS[0]
        self.assertEqual(redact_external(fragment), fragment.replace("fixture-secret", "[已脱敏]"))
        unchanged = f"HTTPS://Example.test/{SOURCE_IDS[0]}?email=buyer%40example.com&phone=13812345678#original-anchor"
        self.assertEqual(redact_external(unchanged), unchanged)

    def test_header_credentials_preserve_contacts_after_chinese_punctuation(self):
        for header in ("Cookie: sid=fixture-secret; session=fixture-session",
                       "Authorization: Bearer fixture-secret", "Bearer fixture-secret"):
            with self.subTest(header=header):
                safe = redact_external(header + "，" + CONTACT)
                self.assertNotIn("fixture-secret", safe)
                self.assertNotIn("fixture-session", safe)
                self.assertTrue(safe.endswith(CONTACT))

    def test_request_output_and_saved_audit_agree_on_original_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SalesStorage(Path(directory) / "fixture.db")
            task = storage.create_task({"keyword": "fixture"})
            captured = []

            def fake_model(messages, **kwargs):
                data = json.loads(messages[-1]["content"])
                captured.append(data)
                output = {"decision": "complete", "confidence": .9, "evidence": [],
                          "risks": [], "next_actions": [], "notes": [
                          {"note_id": item["note_id"], "decision": "keep", "reason": CONTACT}
                          for item in data["notes"]]}
                return output, json.dumps(output)

            runner = StructuredAgentRunner({"llm": {"model": "fixture"}}, storage,
                                           task, llm_json_callable=fake_model)
            runner.run("relevance_agent", {"notes": [{"note_id": identity, "desc": CONTACT}
                                                    for identity in SOURCE_IDS]})
            audit = storage.get_agent_call(runner.last_call_id, task_id=task)
            self.assertEqual([item["note_id"] for item in captured[0]["notes"]], list(SOURCE_IDS))
            self.assertEqual(audit["input"], captured[0])
            self.assertEqual([item["note_id"] for item in audit["output"]["notes"]], list(SOURCE_IDS))
            self.assertEqual(audit["metrics"]["missingCount"], 0)
            self.assertEqual(audit["metrics"]["coveredCount"], 2)

    def test_source_ids_survive_relevance_signal_qa_and_report(self):
        class ContactAdapter(Adapter):
            def comments(self, identity, cursor=""):
                return {"data": {"comments": [{"id": SOURCE_IDS[1], "content": CONTACT,
                        "user_info": {"user_id": "fixture-buyer", "nickname": "合成客户"}}],
                        "has_more": False}}

        adapter = ContactAdapter(pages=lambda *_: [note(identity, CONTACT) for identity in SOURCE_IDS], body=CONTACT)
        runner, progress = Runner(), []
        plan = normalize_plan({"seedKeywords": ["客服工具"], "maxNotes": 2, "maxPagesPerKeyword": 1,
                              "maxCommentsPerNote": 1, "maxCommentsTotal": 2,
                              "delayMin": 0, "delayMax": 0, "scanLeads": True})
        result = HarnessPipeline(adapter, {}, runner=runner).run(
            {"confirmedPlan": plan}, progress_callback=progress.append)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["collectionSummary"]["failures"], [])
        self.assertEqual(result["stageCoverage"]["relevance"]["completed"], 2)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["completed"], 4)
        self.assertEqual(result["stageCoverage"]["qa"]["completed"], 4)
        self.assertTrue(all(lead["qa_status"] == "approved" for lead in result["leads"]["leads"]))
        self.assertEqual([key for key, _ in runner.calls].count("relevance_agent"), 1)
        self.assertEqual(progress[-1]["percent"], 100)
        for lead in result["leads"]["leads"]:
            self.assertIn("13812345678", lead["content"])
            self.assertIn("buyer@example.com", lead["evidence"])

    def test_partial_terminal_progress_preserves_actual_work(self):
        class MissingRunner(Runner):
            def run(self, key, payload, purpose=""):
                result = super().run(key, payload, purpose)
                if key == "relevance_agent":
                    result["notes"] = []
                return result

        progress = []
        plan = normalize_plan({"seedKeywords": ["fixture"], "maxNotes": 1,
                              "delayMin": 0, "delayMax": 0, "scanLeads": False})
        result = HarnessPipeline(Adapter(), {}, runner=MissingRunner()).run(
            {"confirmedPlan": plan}, progress_callback=progress.append)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(progress[-1]["stage"], "partial")
        self.assertEqual(progress[-1]["percent"], progress[-2]["percent"])
        self.assertGreater(progress[-1]["percent"], 0)
        self.assertIn("原始笔记编号", result["collectionSummary"]["failures"][0]["error"])


if __name__ == "__main__":
    unittest.main()
