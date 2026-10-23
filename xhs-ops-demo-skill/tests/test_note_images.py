"""Image collection, multimodal transport and evidence provenance regressions."""
import base64
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from note_images import (MAX_IMAGE_BYTES, checked_image_url, fetch_note_image, image_sources,
                         missing_note_content, note_content, validate_image_output)
from source_normalization import extract_note, merge_note
from agent_harness.pipeline import HarnessPipeline, PipelineStopped
from agent_harness.runtime import StructuredAgentRunner
from agent_harness.planning import normalize_plan
from agent_harness.registry import PromptRegistry
from storage import SalesStorage
from test_evidence_first_pipeline import EvidenceAdapter, EvidenceRunner, source, NOW, BUYER_TEXT

URL = "http://sns-webpic-qc.xhscdn.com/fixture.webp"
COMMON = {"decision": "complete", "confidence": .95, "evidence": [], "risks": [], "next_actions": []}


def output(**values):
    return {**COMMON, "status": "recognized", "text": BUYER_TEXT,
            "visual_description": "文字海报", "uncertain_regions": [], **values}


def image_fixture():
    stream = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(stream, format="PNG")
    body = stream.getvalue()
    return body, {"data_uri": "data:image/png;base64," + base64.b64encode(body).decode(),
                  "sha256": "fixture-hash", "mime": "image/png", "bytes": len(body), "width": 24, "height": 24}


class ImageRunner(EvidenceRunner):
    def __init__(self, result=None):
        super().__init__()
        self.image_result = result or output()
        self.image_inputs = []

    def run(self, key, data, purpose="", **kwargs):
        if key == "image_reading_agent":
            self.calls.append((key, copy.deepcopy(data)))
            self.image_inputs.extend(kwargs.get("image_inputs") or [])
            if isinstance(self.image_result, Exception):
                raise self.image_result
            return copy.deepcopy(self.image_result)
        if key == "relevance_agent":
            self.calls.append((key, copy.deepcopy(data)))
            return {**COMMON, "notes": [{"note_id": n["note_id"], "decision": "keep", "reason": "核对图文", "evidence": []}
                                         for n in data["notes"]]}
        return super().run(key, data, purpose)


class NoteImageTests(unittest.TestCase):
    def pipeline(self, *, images=1, body="#开发[话题]#", reader=None, download=None, **plan_values):
        item = source("gallery", body=body)
        item["note_card"]["tag_list"] = [{"name": "标签独有的智能体"}]
        item["note_card"]["image_list"] = [{"url_default": URL + f"?image={index}"} for index in range(images)]
        runner = ImageRunner(reader)
        adapter = EvidenceAdapter([item], {"gallery": item}, {"gallery": ["什么价格", "我可以"]})
        plan = normalize_plan({"seedKeywords": ["智能体"], "keywords": ["智能体"], "searchGoal": "研究需求",
            "offeringId": "general", "readDetail": True, "scanLeads": True, "delayMin": 0, "delayMax": 0,
            **plan_values})
        with patch("agent_harness.pipeline.fetch_note_image", side_effect=download,
                   return_value=image_fixture()[1]) as fetch, patch("agent_harness.pipeline.time.time", return_value=NOW):
            result = HarnessPipeline(adapter, {}, runner=runner).run({"confirmedPlan": plan})
        return result, runner, fetch

    def test_normalization_retains_gallery_order_missing_urls_and_camel_case(self):
        images = image_sources({"imageList": [{"infoList": [{"imageScene": "WB_PRV", "url": "preview"},
                    {"imageScene": "WB_DFT", "url": URL}]}, {"urlDefault": "second"}, {}]})
        self.assertEqual(images, [{"index": 1, "url": URL}, {"index": 2, "url": "second"}, {"index": 3, "url": ""}])
        base = extract_note(source("fixture"))
        base.update(images=2, image_sources=images[:2])
        self.assertEqual(merge_note(base, extract_note(source("fixture")))["image_sources"], images[:2])
        empty = source("fixture")
        empty["note_card"]["image_list"] = []
        merged = merge_note(base, extract_note(empty))
        self.assertEqual((merged["images"], merged["image_sources"]), (0, []))

    def test_image_only_demand_reaches_relevance_author_comments_qa_and_report(self):
        result, runner, fetch = self.pipeline()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(runner.image_inputs), 1)  # Not repeated for every comment/stage.
        n = result["notes"][0]
        self.assertEqual(n["desc"], "#开发[话题]#")  # Original body stays intact.
        self.assertTrue(n["evidenceComplete"])
        self.assertEqual(n["image_status"], "complete")
        author = next(x for x in result["leads"]["leads"] if x["lead_type"] == "note_author")
        self.assertTrue(author["qualified"])
        self.assertIn(BUYER_TEXT, author["content"])
        for key in ("relevance_agent", "lead_signal_agent", "critic_qa_agent", "report_agent"):
            data = next(value for agent, value in runner.calls if agent == key)
            contexts = data.get("notes", data.get("noteContexts"))
            self.assertIn(BUYER_TEXT, json.dumps(contexts, ensure_ascii=False))
            self.assertIn("标签独有的智能体", json.dumps(contexts, ensure_ascii=False))
        candidates = next(value["leadCandidates"] for key, value in runner.calls if key == "lead_signal_agent")
        comments = [x for x in candidates if x["lead_type"] == "comment_lead"]
        self.assertEqual([x["content"] for x in comments], ["什么价格", "我可以"])

    def test_text_and_images_are_both_read_and_combined_without_visual_description_becoming_a_quote(self):
        result, runner, fetch = self.pipeline(body="这次的文字正文", images=2)
        self.assertEqual(fetch.call_count, 2)
        text = note_content(result["notes"][0])
        self.assertIn("这次的文字正文", text)
        self.assertIn("【图片 2 识别文字】", text)
        self.assertNotIn("文字海报", text)

    def test_download_or_model_failure_is_partial_and_never_evidence_complete(self):
        for kwargs in ({"download": ValueError("图片下载失败")}, {"reader": ValueError("模型接口不支持图片")},
                       {"reader": output(status="partial", uncertain_regions=["右下角模糊"])},
                       {"reader": output(confidence=.4)}):
            with self.subTest(kwargs=kwargs):
                result, runner, fetch = self.pipeline(**kwargs)
                self.assertEqual(result["status"], "partial")
                self.assertFalse(result["notes"][0]["evidenceComplete"])
                self.assertEqual(result["collectionSummary"]["contentCoverage"]["missing"], 1)
                self.assertIn("图片", result["notes"][0]["content_gap"])
                self.assertFalse(any(x["qualified"] for x in result["leads"]["leads"]))

    def test_multi_image_partial_failure_preserves_successful_transcription(self):
        result, _, fetch = self.pipeline(images=2, download=[image_fixture()[1], ValueError("图片过期")])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["notes"][0]["image_evidence"][0]["text"], BUYER_TEXT)
        self.assertFalse(result["notes"][0]["evidenceComplete"])

    def test_disabled_details_do_not_download_images_or_call_vision(self):
        result, runner, fetch = self.pipeline(readDetail=False)
        fetch.assert_not_called()
        self.assertFalse(runner.image_inputs)
        self.assertEqual(result["notes"][0]["image_status"], "not_requested")

    def test_image_count_limit_is_reported_not_silently_complete(self):
        with patch("agent_harness.pipeline.MAX_IMAGES_PER_NOTE", 1):
            result, runner, fetch = self.pipeline(images=2)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "partial")
        self.assertIn("最多自动识别 1 张", result["notes"][0]["content_gap"])

    def test_image_exclusion_and_tag_only_gap(self):
        result, _, _ = self.pipeline(excludeKeywords=["门店客服"])
        self.assertEqual(result["notes"][0]["relevance"]["decision"], "drop")
        self.assertFalse(result["leads"]["leads"])
        self.assertTrue(missing_note_content({"desc": "#开发[话题]# #ai[话题]#"}))
        self.assertTrue(missing_note_content({"desc": "#开发# #ai#"}))
        self.assertFalse(missing_note_content({"desc": "#开发[话题]# 需要帮忙"}))

    def test_cancel_during_download_stops_pipeline(self):
        result, _, _ = self.pipeline(download=PipelineStopped())
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["notes"][0]["evidenceComplete"])

    def test_public_download_validates_bytes_and_does_not_send_credentials(self):
        body, _ = image_fixture()
        requests = []
        def reply(request):
            requests.append(request)
            return httpx.Response(200, content=body)
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            image = fetch_note_image(URL, "xhs", client=client)
        self.assertEqual(image["mime"], "image/png")
        self.assertEqual(image["bytes"], len(body))
        self.assertEqual(requests[0].url.scheme, "https")
        self.assertNotIn("authorization", requests[0].headers)
        self.assertNotIn("cookie", requests[0].headers)

    def test_rejects_non_cdn_credentials_redirects_html_and_oversized_images(self):
        for url in ("http://localhost/private", "https://127.0.0.1/image", "https://xhscdn.com.evil.invalid/img",
                    "file:///private", "https://user:pass@sns-webpic-qc.xhscdn.com/img", ""):
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked_image_url(url, "xhs")
        responses = [httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
                     httpx.Response(200, content=b"<html>login</html>"),
                     httpx.Response(200, headers={"content-length": str(MAX_IMAGE_BYTES + 1)})]
        for response in responses:
            with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
                with self.assertRaises(ValueError):
                    fetch_note_image(URL, "xhs", client=client)

    def test_actual_image_transport_is_audited_without_base64_or_secret(self):
        _, image = image_fixture()
        captured = []
        with tempfile.TemporaryDirectory() as directory:
            storage = SalesStorage(Path(directory) / "audit.db")
            task = storage.create_task({"keyword": "image-fixture"})
            def fake(messages, **kwargs):
                captured.append(messages)
                return output(), json.dumps(output())
            runner = StructuredAgentRunner({"llm": {"model": "fixture", "api_key": "synthetic-secret"}},
                storage, task, registry=PromptRegistry(), llm_json_callable=fake)
            runner.run("image_reading_agent", {"note_id": "fixture", "image": {"index": 1, "sha256": image["sha256"]}},
                       image_inputs=[image["data_uri"]])
            content = captured[0][-1]["content"]
            self.assertEqual(content[1]["image_url"]["url"], image["data_uri"])
            audit = storage.get_agent_call(runner.last_call_id)
            self.assertNotIn(image["data_uri"], json.dumps(audit))
            self.assertNotIn("synthetic-secret", json.dumps(audit))
            self.assertEqual(audit["input"]["image"]["sha256"], "fixture-hash")
            with self.assertRaisesRegex(ValueError, "缺少实际图片"):
                runner.run("image_reading_agent", {})

    def test_inconsistent_ocr_output_is_rejected(self):
        for value in (output(text=""), output(status="no_text"), output(uncertain_regions=None), output(status="invented")):
            with self.assertRaises(ValueError):
                validate_image_output(value)


if __name__ == "__main__":
    unittest.main()
