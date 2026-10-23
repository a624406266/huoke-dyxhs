"""Task-manager integration without network, browser, or real accounts."""
from __future__ import annotations

import copy
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from agent_harness.planning import normalize_plan
from config_loader import DEFAULT_CONFIG


class TaskManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"XHS_DB_PATH": str(Path(self.temp.name) / "test.db"),
            "XHS_ADMIN_PASSWORD": "", "XHS_ADMIN_PASSWORD_HASH": ""})
        env.start()
        self.addCleanup(env.stop)
        spec = importlib.util.spec_from_file_location("isolated_task_manager_server", SCRIPTS / "server.py")
        self.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["storage"]["db_path"] = str(Path(self.temp.name) / "test.db")
        config["auth"]["admin_password_hash"] = ""
        self.server.initialize_app(config)
        self.manager = self.server.task_manager

    def request(self, **updates):
        plan = normalize_plan({"seedKeywords": ["折叠车"], "searchGoal": "通勤选购讨论", "maxNotes": 5,
             "maxCommentsTotal": 10, "maxPagesPerKeyword": 3, "delayMin": 0, "delayMax": 0, **updates})
        saved = self.server.workbench().save_plan(plan)
        return {"planId": saved["planId"], "confirmedPlan": plan, "rounds": 3, "intervalMin": 0, "intervalMax": 0}

    def task(self, request):
        payload = self.server.confirmed_task(request)
        task_id = self.server.STORAGE.create_task(payload, mode="batch", sales_user_id=0)
        return task_id, payload

    def result(self, number=1, comments=1, status="completed", error=""):
        source = {"id": f"n{number}", "platform": "xhs", "title": "通勤选购讨论", "url": f"https://example.test/n{number}"}
        lead = {"lead_id": f"n{number}:c{number}", "platform": "xhs", "note_id": f"n{number}",
                "comment_id": f"c{number}", "content": "想选一辆折叠车", "evidence": "想选一辆折叠车",
                "note_url": source["url"], "score": 80, "qualified": True, "classification": "demand",
                "evidenceComplete": True, "human_review_status": "pending"}
        return {"status": status, "errorText": error, "notes": [source], "platforms": ["xhs"],
                "leads": {"leads": [lead], "leadsCount": 1, "qualifiedCount": 1, "totalCommentsScanned": comments},
                "collectionSummary": {"notesCollected": 1, "commentsCollected": comments, "searchRequests": 1,
                    "endReasons": ["configured_pages_completed"], "failures": [{"stage": "read_detail", "error": error}] if error else []},
                "report": {"summary": f"第{number}批实际结果"}}

    def coverage(self, *, candidate_count=1):
        units = {"search": "query", "relevance": "note", "details": "note", "comments": "note",
                 "lead_signal": "candidate", "qa": "candidate", "report": "report"}
        return {stage: {"status": "completed", "completed": candidate_count if unit == "candidate" else 1,
                        "total": candidate_count if unit == "candidate" else 1, "unit": unit}
                for stage, unit in units.items()}

    def test_flat_plan_and_known_preview_id_are_required(self):
        request = self.request()
        with self.assertRaisesRegex(ValueError, "预览"):
            self.server.confirmed_task({"confirmedPlan": request["confirmedPlan"]})
        with self.assertRaisesRegex(ValueError, "确认"):
            self.server.confirmed_task({"planId": request["planId"], "keyword": "绕过确认"})
        with self.assertRaises(ValueError):
            self.server.confirmed_task({**request, "confirmedPlan": {"plan": request["confirmedPlan"]}})
        confirmed = self.server.confirmed_task({**request, "keyword": "外层覆盖词", "maxNotes": 999})
        self.assertEqual(confirmed["confirmedPlan"], request["confirmedPlan"])
        self.assertEqual(confirmed["keyword"], "折叠车")
        self.assertNotIn("limits", confirmed["confirmedPlan"])

    def test_batch_result_uses_confirmed_metric_across_rounds(self):
        task_id, payload = self.task(self.request(metric="comments"))
        notes = {"a": {"id": "a", "liked": 1000, "comments": 2},
                 "b": {"id": "b", "liked": 1, "comments": 90}}
        result = self.manager._batch_snapshot(task_id, payload, notes, {}, 0, None, [], {})
        self.assertEqual([note["id"] for note in result["notes"]], ["b", "a"])
        self.assertEqual(result["metric"], "comments")
        self.assertEqual(result["metricLabel"], "评论量")

    def test_partial_and_needs_action_survive_task_completion(self):
        for status in ("partial", "needs_action"):
            with self.subTest(status=status):
                task_id, payload = self.task(self.request())
                parent = self
                class Pipeline:
                    def run(self, *args, **kwargs):
                        result = parent.result(status=status, error="需要人工核验")
                        kwargs["progress_callback"]({"stage": status, "percent": 61})
                        kwargs["snapshot_callback"](result)
                        return result
                with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
                    self.manager._run_task(task_id, payload, "single", 0)
                stored = self.server.STORAGE.get_task(task_id)
                self.assertEqual(stored["status"], status)
                self.assertEqual(stored["result"]["status"], status)
                self.assertEqual(stored["progress"]["percent"], 61)
                self.assertNotEqual(stored["progress"]["stage"], "done")

    def test_exception_after_snapshot_preserves_partial_sources(self):
        task_id, payload = self.task(self.request())
        parent = self
        class Pipeline:
            def run(self, *args, **kwargs):
                kwargs["snapshot_callback"](parent.result(status="running"))
                raise RuntimeError("模拟报告阶段故障")
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            self.manager._run_task(task_id, payload, "single", 0)
        stored = self.server.STORAGE.get_task(task_id)
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["result"]["notes"][0]["id"], "n1")
        self.assertIn("报告阶段", stored["error"])

    def test_partial_status_identifies_missing_decisions_without_rewriting_history(self):
        task_id, _ = self.task(self.request())
        result = self.result(status="partial")
        result["stageCoverage"] = self.coverage()
        result["stageCoverage"]["relevance"].update(status="partial", completed=26, total=28)
        result["collectionSummary"]["failures"] = [
            {"stage": "relevance", "source": f"note-{index}", "error": "模型没有返回判断"}
            for index in (1, 2)]
        result["collectionSummary"]["endReasons"] = ["note_quota_reached", "comment_quota_reached"]
        result["leads"].update(qualifiedCount=0)
        result["leads"]["leads"][0].update(qa_status="needs_review", business_review_required=True)
        self.server.STORAGE.update_task(task_id, progress={"stage": "partial", "percent": 0})
        self.server.STORAGE.finish_task(task_id, status="partial", result=result, status_text="部分完成，请查看失败来源。")
        before = self.server.STORAGE.get_task(task_id)
        view = self.manager.get_status(task_id)
        self.assertIn("流程已结束", view["statusText"])
        self.assertIn("相关性判断有 2 条未完成（26/28）", view["statusText"])
        self.assertIn("可查看已有结果", view["statusText"])
        self.assertNotIn("模型质检", view["statusText"])
        self.assertNotIn("评论读取", view["statusText"])
        self.assertEqual(self.server.STORAGE.get_task(task_id), before)

    def test_partial_status_falls_back_to_recorded_failures_and_skips_budget_stages(self):
        result = self.result(status="partial", error="详情失败")
        result["stageCoverage"] = {"comments": {"status": "skipped", "completed": 0, "total": 3,
                                               "reason": "评论预算已耗尽"}}
        text = self.server.partial_task_status_text(result)
        self.assertIn("正文读取记录 1 项异常", text)
        self.assertNotIn("评论读取", text)

    def test_business_review_and_zero_qualified_do_not_change_execution_completion(self):
        task_id, payload = self.task(self.request())
        result = self.result()
        result["stageCoverage"] = self.coverage()
        result["leads"].update(qualifiedCount=0)
        result["leads"]["leads"][0].update(qualified=False, qa_status="needs_review", business_review_required=True)
        result["qa"] = {"approved": False}
        with patch.object(self.manager, "_run_single", return_value=result), patch.object(self.server.evolution_jobs, "start"):
            self.manager._run_task(task_id, payload, "single", 0)
        stored = self.server.STORAGE.get_task(task_id)
        self.assertEqual(stored["status"], "completed")
        self.assertNotIn("未完成", stored["statusText"])

    def test_cross_round_global_budgets_are_remaining_not_repeated(self):
        task_id, payload = self.task({**self.request(maxNotes=3, maxCommentsTotal=3, maxPagesPerKeyword=5), "rounds": 5})
        calls, parent = [], self
        class Pipeline:
            def run(self, round_payload, **kwargs):
                calls.append((copy.deepcopy(round_payload), kwargs["page"]))
                comments = min(2, round_payload["remainingCommentsTotal"])
                result = parent.result(len(calls), comments)
                kwargs["snapshot_callback"](result)
                return result
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual([x[0]["remainingNotes"] for x in calls], [3, 2])
        self.assertEqual([x[0]["remainingCommentsTotal"] for x in calls], [3, 1])
        self.assertEqual(calls[1][0]["seenNoteIds"], ["xhs:n1"])
        self.assertTrue(all(x[0]["confirmedPlan"] == payload["confirmedPlan"] for x in calls))
        self.assertEqual(result["leads"]["totalCommentsScanned"], 3)
        self.assertEqual(result["collectionSummary"]["notesCollected"], 2)

    def test_total_page_limit_is_one_page_per_round_without_multiplication(self):
        task_id, payload = self.task({**self.request(maxPagesPerKeyword=2, maxNotes=20, scanLeads=False), "rounds": 10})
        calls, parent = [], self
        class Pipeline:
            def run(self, round_payload, **kwargs):
                calls.append((round_payload["pageBudget"], kwargs["page"]))
                return parent.result(len(calls), comments=0)
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        self.assertEqual(calls, [(1, 1), (1, 2)])
        self.assertEqual(result["confirmedPlan"]["maxPagesPerKeyword"], 2)

    def test_partial_failure_history_survives_later_success(self):
        task_id, payload = self.task(self.request())
        parent, calls = self, []
        class Pipeline:
            def run(self, round_payload, **kwargs):
                calls.append(kwargs["page"])
                return parent.result(len(calls), status="partial" if len(calls) == 1 else "completed",
                                     error="第一轮详情失败" if len(calls) == 1 else "")
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["collectionSummary"]["failures"][0]["error"], "第一轮详情失败")
        self.assertEqual(result["collectionSummary"]["searchRequests"], 3)
        self.assertEqual(result["report"]["scope"], "latest_round")

    def test_batch_filter_diagnostics_accumulate_once_per_round_not_per_snapshot(self):
        task_id, payload = self.task(self.request(maxPagesPerKeyword=2))
        parent = self
        class Pipeline:
            def run(self, *args, **kwargs):
                number = kwargs['page']
                result = parent.result(number)
                result['collectionSummary'].update(filterCounts={'outside_time_range': number, 'unknown_publish_time': 0},
                    timeResolution={'attempted': number, 'resolved': number, 'unresolved': 0, 'failed': 0},
                    timeReferenceAt=1000 + number, receivedNotes=number + 1, duplicates=0)
                kwargs['snapshot_callback'](result)
                kwargs['snapshot_callback'](result)
                return result
        with patch.object(self.server, 'get_pipeline', return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        summary = result['collectionSummary']
        self.assertEqual(summary['filterCounts']['outside_time_range'], 3)
        self.assertEqual(summary['timeResolution']['attempted'], 3)
        self.assertEqual(summary['receivedNotes'], 5)
        self.assertEqual(summary['notesCollected'], 2)
        self.assertEqual([row['round'] for row in summary['timeReferences']], [1, 2])

    def test_body_coverage_and_stage_reasons_survive_multiple_round_snapshots(self):
        task_id, payload = self.task(self.request(maxPagesPerKeyword=2))
        parent = self
        class Pipeline:
            def run(self, *args, **kwargs):
                number = kwargs["page"]
                failed = number == 1
                result = parent.result(number, status="partial" if failed else "completed")
                result["collectionSummary"].update(filterCounts={}, contentCoverage={
                    "mode": "full_text", "requested": 1, "attempted": 1, "fetched": int(not failed),
                    "complete": int(not failed), "excluded": 0, "missing": int(failed), "failed": int(failed), "pending": 0,
                    "gaps": [{"noteId": "n1", "platform": "xhs", "reason": "正文读取失败"}] if failed else []})
                result["stageCoverage"] = parent.coverage()
                result["stageCoverage"]["comments"].update(reason="已达到评论上限", unscanned=2, limited=1)
                kwargs["snapshot_callback"](result)
                kwargs["snapshot_callback"](result)
                return result
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        content = result["collectionSummary"]["contentCoverage"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual((content["requested"], content["complete"], content["missing"]), (2, 1, 1))
        self.assertEqual(content["gaps"][0]["round"], 1)
        self.assertEqual(result["stageCoverage"]["comments"]["unscanned"], 4)
        self.assertIn("第 1 轮", result["stageCoverage"]["comments"]["reason"])
        self.assertIn("第 2 轮", result["stageCoverage"]["comments"]["reason"])

    def test_completion_copy_distinguishes_zero_leads_summary_and_missing_evidence(self):
        for mode, status, expected in (("full_text", "completed", "本次未发现"),
                                       ("summary", "completed", "正文未核验"),
                                       ("full_text", "partial", "证据或分析缺口")):
            with self.subTest(mode=mode, status=status):
                task_id, payload = self.task(self.request())
                result = self.result(status=status)
                result["leads"].update(leads=[], leadsCount=0, qualifiedCount=0)
                result["collectionSummary"]["contentCoverage"] = {"mode": mode}
                with patch.object(self.manager, "_run_single", return_value=result), patch.object(self.server.evolution_jobs, "start"):
                    self.manager._run_task(task_id, payload, "single", 0)
                self.assertIn(expected, self.server.STORAGE.get_task(task_id)["statusText"])

    def test_batch_exception_snapshot_retains_previous_round_failures(self):
        task_id, payload = self.task(self.request())
        parent, calls = self, []
        class Pipeline:
            def run(self, *args, **kwargs):
                calls.append(kwargs["page"])
                if len(calls) == 1:
                    return parent.result(1, status="partial", error="第一轮详情失败")
                kwargs["snapshot_callback"](parent.result(2, status="running"))
                raise RuntimeError("第二轮报告故障")
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            self.manager._run_task(task_id, payload, "batch", 0)
        stored = self.server.STORAGE.get_task(task_id)
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(len(stored["result"]["notes"]), 2)
        self.assertTrue(any(item.get("error") == "第一轮详情失败"
                            for item in stored["result"]["collectionSummary"]["failures"]))

    def test_round_stage_coverage_preserves_failed_and_pending_history_after_success(self):
        task_id, payload = self.task(self.request(maxPagesPerKeyword=2))
        parent = self
        class Pipeline:
            def run(self, *args, **kwargs):
                number = kwargs["page"]
                result = parent.result(number, status="partial" if number == 1 else "completed",
                                       error="第一轮需求判断超时" if number == 1 else "")
                result["stageCoverage"] = parent.coverage(candidate_count=2 if number == 1 else 3)
                result["qa"] = {"approved": number == 2, "reviewedLeadsCount": 0 if number == 1 else 3,
                                "expectedLeadsCount": 2 if number == 1 else 3}
                if number == 1:
                    result["stageCoverage"]["lead_signal"].update(status="partial", completed=1, error="模型总预算超时")
                    result["stageCoverage"]["qa"].update(status="pending", completed=0)
                    result["stageCoverage"]["report"].update(status="pending", completed=0)
                kwargs["snapshot_callback"](result)
                return result
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stageCoverageScope"], "executed_rounds")
        self.assertEqual(result["stageCoverage"]["search"]["completed"], 2)
        self.assertEqual(result["stageCoverage"]["search"]["total"], 2)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["completed"], 4)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["total"], 5)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["status"], "partial")
        self.assertEqual(result["stageCoverage"]["qa"]["completed"], 3)
        self.assertEqual(result["stageCoverage"]["qa"]["total"], 5)
        self.assertEqual(result["stageCoverage"]["qa"]["status"], "partial")
        self.assertEqual(result["stageCoverage"]["report"]["status"], "partial")
        self.assertEqual(result["roundStageCoverage"][0]["stages"]["qa"]["status"], "pending")
        self.assertEqual(result["qa"]["scope"], "latest_round")
        self.assertEqual(result["qa"]["round"], 2)
        self.assertEqual(result["qa"]["reviewedLeadsCount"], 3)
        self.assertEqual(result["roundQa"][0]["qa"]["reviewedLeadsCount"], 0)
        self.assertIn("第 1 轮", result["errorText"])
        status = self.manager.get_status(task_id, sales_user_id=0)
        self.assertEqual(status["stageCoverage"], result["stageCoverage"])
        self.assertEqual(status["roundStageCoverage"], result["roundStageCoverage"])

    def test_repeated_live_snapshots_replace_round_counts_instead_of_accumulating(self):
        task_id, payload = self.task(self.request(maxPagesPerKeyword=2))
        parent, live = self, []
        class Pipeline:
            def run(self, *args, **kwargs):
                number = kwargs["page"]
                result = parent.result(number, status="running")
                result["stageCoverage"] = parent.coverage(candidate_count=2)
                result["stageCoverage"]["lead_signal"].update(status="running", completed=1)
                for _ in range(3):
                    kwargs["snapshot_callback"](result)
                    live.append(parent.server.STORAGE.get_task(task_id)["result"]["stageCoverage"]["lead_signal"].copy())
                result["stageCoverage"]["lead_signal"].update(status="completed", completed=2)
                result["status"] = "completed"
                kwargs["snapshot_callback"](result)
                return result
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            result = self.manager._run_batch(task_id, payload, 0)
        self.assertEqual([row["completed"] for row in live], [1, 1, 1, 3, 3, 3])
        self.assertEqual([row["total"] for row in live], [2, 2, 2, 4, 4, 4])
        self.assertTrue(all(row["status"] == "running" for row in live))
        self.assertEqual(result["stageCoverage"]["lead_signal"]["completed"], 4)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["total"], 4)
        self.assertEqual(result["stageCoverage"]["lead_signal"]["status"], "completed")
        self.assertEqual([row["round"] for row in result["roundStageCoverage"]], [1, 2])

    def test_unexpected_batch_exception_finalizes_running_stage_and_keeps_prior_round(self):
        task_id, payload = self.task(self.request(maxPagesPerKeyword=2))
        parent = self
        class Pipeline:
            def run(self, *args, **kwargs):
                number = kwargs["page"]
                result = parent.result(number, status="completed" if number == 1 else "running")
                result["stageCoverage"] = parent.coverage(candidate_count=1 if number == 1 else 2)
                if number == 2:
                    result["stageCoverage"]["qa"].update(status="running", completed=1)
                    result["stageCoverage"]["report"].update(status="pending", completed=0)
                kwargs["snapshot_callback"](result)
                if number == 2:
                    raise RuntimeError("模拟第二轮质检异常")
                return result
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            self.manager._run_task(task_id, payload, "batch", 0)
        stored = self.server.STORAGE.get_task(task_id)
        result = stored["result"]
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(result["stageCoverage"]["qa"]["status"], "partial")
        self.assertEqual(result["stageCoverage"]["qa"]["completed"], 2)
        self.assertEqual(result["stageCoverage"]["qa"]["total"], 3)
        self.assertEqual(len(result["notes"]), 2)
        self.assertEqual(len(result["roundStageCoverage"]), 2)
        self.assertEqual(result["roundStageCoverage"][-1]["status"], "failed")
        self.assertEqual(result["roundStageCoverage"][-1]["stages"]["qa"]["status"], "partial")
        self.assertIn("第二轮质检异常", result["stageCoverage"]["qa"]["error"])

    def test_stop_prevents_next_round_and_persists_stopped_status(self):
        task_id, payload = self.task(self.request())
        parent, calls = self, []
        class Pipeline:
            def run(self, *args, **kwargs):
                calls.append(kwargs["page"])
                parent.manager.stop_flags[task_id] = True
                return parent.result()
        with patch.object(self.server, "get_pipeline", return_value=Pipeline()):
            self.manager._run_task(task_id, payload, "batch", 0)
        self.assertEqual(calls, [1])
        self.assertEqual(self.server.STORAGE.get_task(task_id)["status"], "stopped")

    def test_rerun_links_parent_and_preserves_review_only_for_identical_basis(self):
        original_id, original_payload = self.task(self.request())
        original_result = self.result()
        self.server.STORAGE.update_task(original_id, result=original_result)
        state = self.server.workbench()
        state.review({"taskId": original_id, "leadId": "n1:c1", "status": "approved", "note": "人工核实原文"}, 0)
        rerun_id, rerun_payload = self.task({**original_payload, "parentTaskId": original_id})
        self.assertNotEqual(original_id, rerun_id)
        self.assertEqual(rerun_payload["parentTaskId"], original_id)
        self.assertEqual(rerun_payload["confirmedPlan"], original_payload["confirmedPlan"])
        rerun_result = self.result()
        self.server.STORAGE.update_task(rerun_id, result=rerun_result)
        # Simulate fresh machine output. It must not override the independent human review.
        self.server.STORAGE.save_lead(rerun_id, rerun_result["leads"]["leads"][0])
        annotated = state.annotate(rerun_result, rerun_payload["confirmedPlan"])
        self.assertEqual(annotated["leads"]["leads"][0]["review"]["status"], "approved")
        changed = self.result()
        changed["leads"]["leads"][0]["content"] = "原文已经改变"
        changed = state.annotate(changed, rerun_payload["confirmedPlan"])
        self.assertEqual(changed["leads"]["leads"][0]["review"]["status"], "pending")
        self.assertEqual(len(state.review_history("n1:c1")), 1)


if __name__ == "__main__":
    unittest.main()
