from __future__ import annotations

import copy
import hashlib
import heapq
import inspect
import json
import random
import time
import uuid
from collections import Counter, deque
from typing import Any, Iterator

from source_normalization import METRIC_LABELS, extract_note, merge_note
from note_dates import classify_note_time
from json_utils import canonical_json
from llm_client import has_llm_key
from note_images import MAX_IMAGES_PER_NOTE, fetch_note_image, missing_note_content, note_content
from .planning import normalize_plan
from .offering import SERVICE_POLICY_VERSION, apply_offering_decision, inquiry_review
from .registry import PromptRegistry
from .runtime import StructuredAgentRunner, redact_external, validate_agent_business_output


class PipelineStopped(Exception):
    pass


class PlatformNeedsAction(Exception):
    pass


class HarnessPipeline:
    def __init__(self, adapter: Any, config: dict[str, Any], storage: Any | None = None,
                 runner: StructuredAgentRunner | None = None) -> None:
        self.adapter, self.config, self.storage, self.runner = adapter, config, storage, runner
        self._active_run_id = ""
        self._active_run_type = "main"
        self._active_platforms: list[str] = []

    def run(self, payload: dict[str, Any], task_id: int | None = None, page: int = 1,
            stop_checker: Any | None = None, event_callback: Any | None = None,
            progress_callback: Any | None = None, snapshot_callback: Any | None = None) -> dict[str, Any]:
        if not isinstance(payload.get("confirmedPlan"), dict):
            raise ValueError("请先预览、编辑并确认搜索计划；执行阶段不能自动扩词。")
        plan = normalize_plan(payload["confirmedPlan"])
        if not self.runner and not has_llm_key((self.config.get("llm") or {}).get("api_key"), (self.config.get("llm") or {}).get("api_base")):
            raise ValueError("请先配置模型 API Key。")
        self._active_run_id = str(payload.get("runId") or uuid.uuid4())
        self._active_run_type = str(payload.get("runType") or "main")
        self._active_platforms = plan["platforms"]
        self._stop = stop_checker or (lambda: False)
        self._emit = event_callback or (lambda *args: None)
        self._progress = progress_callback or (lambda data: None)
        self._last_progress_percent = 0
        self._snapshot = snapshot_callback
        self._task_id = task_id
        self._plan = plan
        self._time_reference = time.time()
        self._page_budget = min(plan["maxPagesPerKeyword"], int(payload.get("pageBudget", plan["maxPagesPerKeyword"])))
        if self._page_budget < 1:
            raise ValueError("本轮 pageBudget 必须为正整数。")
        self._last_read = 0.0
        self._seen_note_ids = set(str(x) for x in payload.get("seenNoteIds", []))
        self._seen_comment_ids = set(str(x) for x in payload.get("seenCommentIds", []))
        remaining = payload.get("remaining") or {}
        note_budget = min(plan["maxNotes"], max(0, int(payload.get("remainingNotes", remaining.get("maxNotes", plan["maxNotes"])))))
        comment_budget = min(plan["maxCommentsTotal"], max(0, int(payload.get("remainingCommentsTotal", remaining.get("maxCommentsTotal", plan["maxCommentsTotal"])))))
        self._summary = {"requested": {key: plan[key] for key in ("maxNotes", "maxCommentsTotal", "maxPagesPerKeyword", "maxCommentsPerNote")},
                         "effectiveMaxNotes": note_budget, "effectiveMaxCommentsTotal": comment_budget,
                         "searchRequests": 0, "receivedNotes": 0, "notesCollected": 0,
                         "commentsCollected": 0, "duplicates": 0, "failures": [], "endReasons": [],
                         "timeReferenceAt": self._time_reference,
                         "filterCounts": {key: 0 for key in ("invalid_record", "non_note", "missing_note_id",
                             "duplicate", "excluded_keyword", "outside_time_range", "unknown_publish_time", "capacity_limit")},
                         "timeResolution": {"attempted": 0, "resolved": 0, "unresolved": 0, "failed": 0, "interrupted": 0}}
        self._notes: list[dict[str, Any]] = []
        self._leads: list[dict[str, Any]] = []
        self._comments: list[dict[str, Any]] = []
        self._content_targets: list[dict[str, Any]] = []
        self._saved_note_fingerprints: dict[tuple[str, str], str] = {}
        self._saved_lead_fingerprints: dict[tuple[str, str], str] = {}
        self._report: dict[str, Any] = {}
        self._qa: dict[str, Any] = {}
        self._stage_coverage = {
            key: {"status": "pending", "completed": 0, "total": 1 if key == "report" else 0, "unit": unit}
            for key, unit in (("search", "query"), ("relevance", "note"), ("details", "note"),
                              ("comments", "note"), ("lead_signal", "candidate"),
                              ("qa", "candidate"), ("report", "report"))
        }
        self._active_stage = ""
        self._status, self._error = "running", ""
        registry = PromptRegistry(self.storage)
        registry.ensure_defaults()
        runner = self.runner or StructuredAgentRunner(self.config, self.storage, task_id, registry,
                    run_id=self._active_run_id, run_type=self._active_run_type)
        self._image_runner = runner
        try:
            self._check_stop()
            self._audit_stage(task_id, stage="plan", agent_key="confirmed_plan", decision="confirmed",
                              reason="仅执行用户已确认的固定计划。", payload={"plan": plan})
            if note_budget == 0:
                self._summary["endReasons"].append("note_quota_reached")
                for stage in self._stage_coverage:
                    self._stage_coverage[stage]["status"] = "skipped"
            else:
                self._start_stage("search", len(plan["keywords"]) * len(plan["platforms"]))
                for platform in plan["platforms"]:
                    self._read(self._adapter_for(platform).ensure_authenticated)
                self._collect(plan, note_budget, page)
                self._finish_stage("search")
                if plan["readDetail"]:
                    # Search cards often contain only a title. Read all retained,
                    # budgeted sources before allowing a relevance rejection.
                    self._details(self._notes)
                    self._prune_time_exclusions()
                else:
                    for note in self._notes:
                        if note.get("images"):
                            note["image_status"] = "not_requested"
                    self._skip_stage("details", "计划未启用正文读取，仅处理搜索摘要。")
                self._refresh_content_coverage()
                self._relevance(runner)
                selected = [n for n in self._notes if (n.get("relevance") or {}).get("decision") in {"keep", "needs_content"}]
                self._summary["unresolvedNotes"] = [n["id"] for n in self._notes if self._missing_content(n)]
                if plan.get("offeringId") == "agent_custom_development":
                    selected.sort(key=self._collection_rank)
                if plan["scanLeads"]:
                    self._collect_comments(selected, comment_budget)
                else:
                    self._skip_stage("comments", "计划未启用评论采集。")
                self._identify(runner, selected)
                self._check_stop()
                if self._leads:
                    self._review_leads(runner)
                else:
                    self._skip_stage("qa", "没有进入需求研判的候选，无需质检。")
                self._check_stop()
                self._start_stage("report", 1)
                report_leads = (heapq.nsmallest(20, self._leads, key=lambda lead: (
                    not lead.get("qualified"), lead.get("classification") != "uncertain", -lead.get("score", 0)))
                    if plan.get("offeringId") == "agent_custom_development" else self._leads[:20])
                self._report = self._agent(runner, "report_agent", {"plan": plan, "notesCount": len(self._notes),
                    "leadsCount": len(self._leads), "topLeads": [self._model_lead(lead) for lead in report_leads],
                    "noteContexts": self._note_contexts([lead["source_comment"] for lead in report_leads]),
                    "qualifiedCount": sum(1 for lead in self._leads if lead.get("qualified")),
                    "classificationCounts": dict(Counter(lead["classification"] for lead in self._leads)),
                    "reportScope": {"included": len(report_leads), "total": len(self._leads), "summaryLimit": 20},
                    **self._report_sources(),
                    "collectionSummary": self._summary,
                    "qa": self._qa}, "生成有来源的研究报告")
                for key in ("productDistribution", "recommended_products", "matched_products"):
                    self._report.pop(key, None)
                self._stage_coverage["report"]["completed"] = 1
                self._finish_stage("report")
                self._check_stop()
            self._status = "partial" if self._summary["failures"] else "completed"
        except PipelineStopped:
            self._status, self._error = "stopped", "已停止，保留已采集内容；未完成部分不视为成功。"
            self._stage_error(self._active_stage, self._error)
        except PlatformNeedsAction as exc:
            self._status, self._error = "needs_action", str(exc)
            self._stage_error(self._active_stage, self._error)
        except Exception as exc:
            self._status = "partial" if self._notes or self._leads else "failed"
            self._error = str(redact_external(str(exc)))[:600]
            self._stage_error(self._active_stage, self._error)
            self._summary["failures"].append({"stage": self._active_stage or "pipeline", "error": self._error})
        self._prune_time_exclusions()
        self._flush(self._status, 100 if self._status == "completed" else None)
        return self._result()

    def _start_stage(self, stage: str, total: int) -> None:
        self._active_stage = stage
        self._stage_coverage[stage].update(status="running", completed=0, total=max(0, int(total)))
        self._publish_coverage()

    def _finish_stage(self, stage: str) -> None:
        row = self._stage_coverage[stage]
        if row.get("error"):
            row["status"] = "partial" if row["completed"] else "failed"
        elif not row["total"]:
            row["status"] = "skipped"
            row["reason"] = ("上游存在未解决的采集或证据缺口，本阶段没有可处理对象。"
                if self._summary["failures"] else "当前范围没有可处理对象，无需执行。")
        else:
            row["status"] = "completed"
        self._publish_coverage()
        if self._active_stage == stage:
            self._active_stage = ""

    def _skip_stage(self, stage: str, reason: str = "当前范围没有可处理对象，无需执行。") -> None:
        self._stage_coverage[stage].update(status="skipped", completed=0, total=0, reason=reason)
        self._publish_coverage()

    def _publish_coverage(self) -> None:
        if self._snapshot:
            self._snapshot(copy.deepcopy(self._result()))

    def _stage_error(self, stage: str, error: str) -> None:
        if stage not in self._stage_coverage:
            return
        row = self._stage_coverage[stage]
        row["error"] = str(redact_external(str(error)))[:500]
        row["status"] = "partial" if row["completed"] else "failed"

    def _check_stop(self) -> None:
        if self._stop():
            raise PipelineStopped()

    def _wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_stop()
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def _read(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        """Three total attempts; only transient READ errors are retried here."""
        for attempt in range(3):
            self._check_stop()
            if self._last_read:
                self._wait(random.uniform(self._plan["delayMin"], self._plan["delayMax"]))
            self._last_read = time.monotonic()
            try:
                result = method(*args, **kwargs)
                if isinstance(result, dict) and (result.get("ok") is False or result.get("success") is False):
                    raise RuntimeError(str(result.get("error") or result.get("message") or "平台返回失败"))
                return result
            except (PipelineStopped, PlatformNeedsAction):
                raise
            except Exception as exc:
                message = str(redact_external(str(exc)))
                lowered = message.lower()
                if any(term in lowered for term in ("captcha", "verification", "cookie", "unauth", "未登录", "登录态", "验证码", "风控", "ip异常", "ip 异常", "401", "403")):
                    raise PlatformNeedsAction(message) from exc
                transient = isinstance(exc, (TimeoutError, ConnectionError)) or any(term in lowered for term in
                    ("timeout", "timed out", "超过", "秒未返回", "connection", "network", "temporar", "429", "502", "503", "504"))
                if not transient or attempt == 2:
                    raise
                self._emit("warning", "读取重试", f"临时读取失败，准备第 {attempt + 2}/3 次尝试。", {"error": message[:300]})
                self._wait(min(2 ** attempt, 4))
        raise RuntimeError("读取失败")

    def _failure(self, stage: str, source: str, exc: Exception) -> None:
        if isinstance(exc, (PipelineStopped, PlatformNeedsAction)):
            raise exc
        detail = {"stage": stage, "source": source, "error": str(redact_external(str(exc)))[:500]}
        self._stage_error({"read_detail": "details", "time_filter": "search"}.get(stage, stage), detail["error"])
        self._summary["failures"].append(detail)
        self._emit("warning", stage, "部分内容未完成，已保留已取得的来源。", detail)

    def _agent(self, runner: Any, key: str, data: dict[str, Any], purpose: str,
               *, image_inputs: list[str] | None = None) -> dict[str, Any]:
        self._check_stop()
        # Even injected runners receive minimized payloads; source originals remain local.
        options = {"image_inputs": image_inputs} if image_inputs else {}
        result = runner.run(key, redact_external(data), purpose=purpose, **options)
        self._check_stop()
        validate_agent_business_output(key, result)
        self._audit_stage(self._task_id, stage=key.removesuffix("_agent"), agent_key=key,
                          decision="completed", reason=purpose, evidence=result.get("evidence"),
                          confidence=result.get("confidence"))
        return result

    def _collect(self, plan: dict[str, Any], budget: int, first_page: int) -> None:
        # Reserve a fair share for each confirmed keyword/platform before using overflow.
        # Retain at most `budget` overflow notes in total, never one full buffer per query.
        lanes = [{"platform": platform, "keyword": keyword, "nextPage": first_page,
                  "pages": 0, "exhausted": False, "visited": False, "buffer": deque(),
                  "collected": 0, "requests": 0}
                 for keyword in plan["keywords"] for platform in plan["platforms"]]
        seen: set[tuple[str, str]] = set()
        buffered = 0

        def accept(lane: dict[str, Any], note: dict[str, Any]) -> None:
            self._notes.append(note)
            lane["collected"] += 1

        def coverage() -> None:
            self._summary["keywordCoverage"] = [
                {"platform": lane["platform"], "keyword": lane["keyword"],
                 "notesCollected": lane["collected"], "requests": lane["requests"],
                 "visited": lane["visited"], "exhausted": lane["exhausted"]}
                for lane in lanes]

        while len(self._notes) < budget:
            active = [lane for lane in lanes if not lane["exhausted"] and lane["pages"] < self._page_budget]
            if not active:
                break
            for index, lane in enumerate(active):
                self._check_stop()
                remaining = budget - len(self._notes)
                if not remaining:
                    break
                allowance = (remaining + len(active) - index - 1) // (len(active) - index)
                platform, keyword = lane["platform"], lane["keyword"]
                adapter = self._adapter_for(platform)
                kwargs = {"page": lane["nextPage"]}
                if "sort" in inspect.signature(adapter.search).parameters:
                    kwargs["sort"] = plan["sort"]
                elif plan["sort"] != "popular":
                    raise ValueError(f"{platform} 当前读取接口不支持 {plan['sort']} 排序，请修改计划。")
                lane["pages"] += 1
                lane["nextPage"] += 1
                lane["requests"] += 1
                audit_buffer = []
                try:
                    raw = self._read(adapter.search, keyword, **kwargs)
                    self._summary["searchRequests"] += 1
                    if not lane["visited"]:
                        lane["visited"] = True
                        self._stage_coverage["search"]["completed"] += 1
                    data = raw.get("data") or raw
                    items = data.get("items") or []
                    self._summary["receivedNotes"] += len(items)
                    accepted = 0
                    for position, item in enumerate(items):
                        self._check_stop()
                        fallback_id = f"search:{kwargs['page']}:{position}"
                        if not isinstance(item, dict):
                            audit_buffer.append(self._record_filter(lane, {"id": fallback_id}, "invalid_record", "搜索结果不是有效对象"))
                            continue
                        if item.get("model_type") != "note":
                            audit_buffer.append(self._record_filter(lane, {"id": str(item.get("id") or fallback_id)},
                                "non_note", "搜索结果为非笔记项目"))
                            continue
                        note = extract_note({**item, "platform": platform}, now=self._time_reference)
                        if not note:
                            audit_buffer.append(self._record_filter(lane, {"id": fallback_id}, "missing_note_id", "笔记缺少来源编号，无法读取和核对"))
                            continue
                        identity = (platform, str(note["id"]))
                        if identity in seen or str(note["id"]) in self._seen_note_ids or f"{platform}:{note['id']}" in self._seen_note_ids:
                            self._summary["duplicates"] += 1
                            audit_buffer.append(self._record_filter(lane, note, "duplicate", "本任务已检查过同一来源"))
                            continue
                        # Remember rejected sources too, so another keyword does not
                        # repeat a failed/unknown-date detail lookup for the same note.
                        seen.add(identity)
                        text = f"{note.get('title', '')} {note.get('desc', '')}".casefold()
                        if any(term.casefold() in text for term in plan["excludeKeywords"]):
                            audit_buffer.append(self._record_filter(lane, note, "excluded_keyword", "命中用户确认的排除词"))
                            continue
                        note.update(source_keyword=keyword, collectedAt=time.time(), evidenceComplete=False,
                                    sourceRefs=[{"platform": platform, "noteId": note["id"], "url": note["url"]}])
                        if accepted >= allowance and buffered >= budget:
                            audit_buffer.append(self._record_filter(lane, note, "capacity_limit", "本查询份额及任务候选缓冲已满，未继续读取详情"))
                            continue
                        check = self._time_check(note, plan["timeFilter"])
                        if check["status"] == "unknown" and plan["readDetail"]:
                            resolution = self._summary["timeResolution"]
                            resolution["attempted"] += 1
                            self._emit("info", "时间核验", "搜索摘要缺少可核验的发布时间，读取详情补证。",
                                       {"platform": platform, "noteId": note["id"], "reason": check["reason"]})
                            try:
                                self._fetch_detail(note, error_stage="time_filter")
                            except (PipelineStopped, PlatformNeedsAction):
                                resolution["interrupted"] += 1
                                raise
                            check = self._time_check(note, plan["timeFilter"])
                            resolution["resolved" if check["status"] != "unknown" else "unresolved"] += 1
                            if note.get("read_error"):
                                resolution["failed"] += 1
                            audit_buffer.append({"stage": "time_filter", "entity_type": "note", "entity_id": note["id"],
                                "platform": platform, "source_keyword": keyword, "decision": check["status"],
                                "reason": check["reason"], "payload": {"timeFilterCheck": check,
                                    "dateEvidence": redact_external(note.get("dateEvidence") or {}),
                                    "detailFetched": bool(note.get("detailFetched"))}})
                        if (note.get("relevance") or {}).get("decision") == "drop":
                            audit_buffer.append(self._record_filter(lane, note, "excluded_keyword", "详情原文命中用户确认的排除词"))
                            continue
                        if check["status"] != "in_range":
                            code = "outside_time_range" if check["status"] == "out_of_range" else "unknown_publish_time"
                            audit_buffer.append(self._record_filter(lane, note, code, check["reason"]))
                            continue
                        if accepted < allowance and len(self._notes) < budget:
                            accept(lane, note)
                            accepted += 1
                        elif buffered < budget:
                            lane["buffer"].append(note)
                            buffered += 1
                    if not items or data.get("has_more") is False:
                        lane["exhausted"] = True
                        self._summary["endReasons"].append(f"{platform}:{keyword}:no_more_results")
                    coverage()
                    self._flush("search", 25)
                except Exception as exc:
                    self._failure("search", f"{platform}:{keyword}:{kwargs['page']}", exc)
                    lane["exhausted"] = True
                    coverage()
                finally:
                    self._save_audit(self._task_id, audit_buffer)
            # Reuse already returned unique results when another query's fair share was empty.
            while buffered and len(self._notes) < budget:
                for lane in lanes:
                    self._check_stop()
                    if lane["buffer"] and len(self._notes) < budget:
                        accept(lane, lane["buffer"].popleft())
                        buffered -= 1
                coverage()
                self._flush("search", 25)
        coverage()
        remaining_audits = []
        for lane in lanes:
            while lane["buffer"]:
                remaining_audits.append(self._record_filter(lane, lane["buffer"].popleft(),
                    "capacity_limit", "已达到全任务笔记上限，未纳入最终结果"))
        self._save_audit(self._task_id, remaining_audits)
        counts = self._summary["filterCounts"]
        if counts["unknown_publish_time"]:
            self._failure("time_filter", "publication_time", ValueError(
                f"{counts['unknown_publish_time']} 条来源的发布时间仍待核验，未纳入所选时间范围；不能据此判断没有需求。"))
        if not self._notes:
            if counts["unknown_publish_time"]:
                self._summary["zeroResultReason"] = "没有可确认在所选时间范围内的笔记；存在发布时间待核验项，请查看过滤明细。"
            elif self._summary["failures"]:
                self._summary["zeroResultReason"] = "采集或读取未完整完成，没有可用笔记；请先处理失败来源。"
            elif self._summary["receivedNotes"]:
                self._summary["zeroResultReason"] = "返回内容经时间范围、排除项及来源类型筛选后无可用笔记，具体数量见过滤明细。"
            else:
                self._summary["zeroResultReason"] = "平台在已执行的关键词与页数范围内没有返回结果。"
        self._emit("info", "采集筛选", "搜索及本地筛选结束，已记录过滤明细。",
                   {"received": self._summary["receivedNotes"], "retained": len(self._notes),
                    "filterCounts": dict(counts), "timeResolution": dict(self._summary["timeResolution"])})
        self._summary["endReasons"].append("note_quota_reached" if len(self._notes) >= budget else "configured_pages_completed")

    def _record_filter(self, lane: dict[str, Any], note: dict[str, Any], code: str, reason: str) -> dict[str, Any]:
        self._summary["filterCounts"][code] += 1
        return {"stage": "search_filter", "entity_type": "note", "entity_id": str(note.get("id") or ""),
                "platform": lane["platform"], "source_keyword": lane["keyword"], "decision": code, "reason": reason,
                "payload": redact_external({"publish_date": note.get("publish_date"),
                    "publish_time_ms": note.get("publish_time_ms"), "dateEvidence": note.get("dateEvidence"),
                    "timeFilterCheck": note.get("timeFilterCheck")})}

    def _time_check(self, note: dict[str, Any], selected: str) -> dict[str, Any]:
        check = {**classify_note_time(note, selected, getattr(self, "_time_reference", time.time())),
                 "selected": selected, "referenceAt": getattr(self, "_time_reference", None)}
        check["reasonCode"] = check["reason"]
        check["reason"] = {
            "unrestricted": "本次未限制发布时间", "within_window": "发布时间在确认范围内",
            "older_than_window": "发布时间早于所选时间范围", "missing_time": "来源未提供发布时间",
            "update_only": "来源仅提供编辑时间，不能据此确认发布时间",
            "unrecognized_time": "发布时间文本无法解析", "invalid_time": "发布时间字段无效",
            "future_time": "时间晚于本次检索参考时点，无法确认发布时间",
            "boundary_precision": "日期精度不足以判断是否位于时间范围边界内",
            "unsupported_time_filter": "不支持的时间范围",
        }.get(check["reasonCode"], "无法确认来源发布时间")
        note["timeFilterCheck"] = check
        return check


    def _relevance(self, runner: Any) -> None:
        self._start_stage("relevance", len(self._notes))
        # Platform-local batches avoid ambiguity if two platforms use the same note ID.
        batches = (chunk for platform in self._plan["platforms"]
                   for chunk in self._chunks([n for n in self._notes if n["platform"] == platform], 12))
        for chunk in batches:
            decisions = {str(n["id"]): n["relevance"] for n in chunk
                         if (n.get("relevance") or {}).get("decision") == "drop"}
            decisions.update({str(n["id"]): {"decision": "needs_content", "reason": "正文证据未完整取得，保留来源与独立评论待核验，不能仅凭标题排除。",
                         "collection_priority": "normal", "coverage_status": "source_missing"}
                         for n in chunk if self._missing_content(n) and str(n["id"]) not in decisions})
            batch_error = None
            try:
                for attempt in range(2):
                    missing = [note for note in chunk if str(note["id"]) not in decisions]
                    if not missing:
                        break
                    output = self._agent(runner, "relevance_agent", {"plan": self._plan,
                        "notes": [self._model_note(self._note_brief(n)) for n in missing],
                        "reviewMode": "missing_batch" if attempt else "initial"}, "按确认计划判断相关性")
                    decisions.update(self._note_decisions(output.get("notes") or [], "retry" if attempt else "initial"))
            except Exception as exc:
                batch_error = exc
            for note in chunk:
                if batch_error and str(note["id"]) not in decisions:
                    continue
                decision = decisions.get(str(note["id"])) or {"decision": "missing_decision", "reason": "未取得与原始笔记编号匹配的相关性判断"}
                note["relevance"] = decision
                if decision["decision"] == "missing_decision":
                    self._summary["failures"].append({"stage": "relevance", "source": note["id"], "error": decision["reason"]})
                    self._stage_error("relevance", decision["reason"])
                else:
                    self._stage_coverage["relevance"]["completed"] += 1
                self._save_audit(self._task_id, [{"platform": note["platform"], "stage": "relevance", "entity_type": "note",
                    "entity_id": note["id"], "decision": decision["decision"], "reason": decision.get("reason", ""),
                    "source_keyword": note.get("source_keyword", ""), "call_id": getattr(runner, "last_call_id", ""),
                    "evidence": decision.get("evidence", [])}])
            self._flush("relevance", 40)
            if batch_error:
                raise batch_error
        self._finish_stage("relevance")

    @staticmethod
    def _missing_content(note: dict[str, Any]) -> bool:
        return missing_note_content(note)

    def _fetch_detail(self, note: dict[str, Any], *, error_stage: str = "read_detail") -> None:
        note["detailAttempted"] = True
        try:
            raw = self._read(self._adapter_for(note["platform"]).read_note, note["id"])
            items = (raw.get("data") or raw).get("items") or []
            detail = next((extract_note({**item, "platform": note["platform"]}, now=self._time_reference) for item in items
                           if str(item.get("id") or (item.get("note_card") or {}).get("note_id")) == str(note["id"])), None)
            if not detail:
                raise ValueError("详情未返回对应来源，保留搜索摘要并标记证据不完整。")
            note.update(merge_note(note, detail))
            # A title or a retained search snippet is not full-body evidence.
            note["desc"] = detail.get("desc") or ""
            note["evidenceComplete"] = not self._missing_content(note)
            note["detailFetched"] = True
            if any(term.casefold() in (str(note.get("title", "")) + " " + str(note.get("desc", ""))).casefold()
                   for term in self._plan["excludeKeywords"]):
                note["relevance"] = {"decision": "drop", "reason": "完整原文命中用户确认的排除词"}
            if error_stage != "time_filter":
                # A search snippet is provisional evidence. A later, more
                # precise publication date must still satisfy the fixed plan.
                check = self._time_check(note, self._plan["timeFilter"])
                if check["status"] != "in_range":
                    note["time_filter_excluded"] = True
                    note["relevance"] = {"decision": "drop", "reason": "详情时间复核：" + check["reason"]}
                    code = "outside_time_range" if check["status"] == "out_of_range" else "unknown_publish_time"
                    lane = {"platform": note["platform"], "keyword": note.get("source_keyword", "")}
                    self._save_audit(self._task_id, [self._record_filter(lane, note, code, "详情时间复核：" + check["reason"])])
                    if code == "unknown_publish_time":
                        self._failure("time_filter", note["id"], ValueError("详情中的发布时间仍待核验，未纳入所选范围。"))
                    # Retain the inspected source and its exclusion in the audit
                    # store while removing it from the final in-scope results.
                    if self.storage and self._task_id:
                        self.storage.save_note(self._task_id, note)
        except Exception as exc:
            note["read_error"] = str(redact_external(str(exc)))[:300]
            self._failure(error_stage, note["id"], exc)

    def _prune_time_exclusions(self) -> None:
        before = len(self._notes)
        self._notes = [note for note in self._notes if not note.get("time_filter_excluded")]
        if before != len(self._notes):
            kept = Counter((note["platform"], note.get("source_keyword")) for note in self._notes)
            for lane in self._summary.get("keywordCoverage", []):
                lane["notesCollected"] = kept[(lane["platform"], lane["keyword"])]
        if before and not self._notes:
            self._summary["zeroResultReason"] = ("详情复核后，来源发布时间仍待核验，未纳入所选时间范围。"
                if self._summary["filterCounts"]["unknown_publish_time"] else
                "详情复核后，候选笔记发布时间均不符合所选时间范围；不能据此判断没有需求。")

    def _details(self, notes: list[dict[str, Any]]) -> None:
        targets = {(n["platform"], n["id"]): n for n in notes}
        self._content_targets = list(targets.values())
        self._start_stage("details", len(targets))
        for note in targets.values():
            self._check_stop()
            if not note.get("detailFetched") and not note.get("read_error"):
                self._fetch_detail(note)
            if note.get("detailFetched") and not note.get("time_filter_excluded"):
                self._read_images(note, self._image_runner)
                note["evidenceComplete"] = not self._missing_content(note)
                if any(term.casefold() in note_content(note).casefold() for term in self._plan["excludeKeywords"]):
                    note["relevance"] = {"decision": "drop", "reason": "笔记正文、标签或图片文字命中用户确认的排除词"}
            if note.get("detailFetched") and (not self._missing_content(note) or self._verified_time_exclusion(note)):
                self._stage_coverage["details"]["completed"] += 1
            elif note.get("read_error"):
                self._stage_error("details", note["read_error"])
            elif not note.get("time_filter_excluded"):
                note["content_gap"] = note.get("content_gap") or "详情没有可用正文（空白或仅话题标签），图文或视频内容尚待核验。"
                self._failure("read_detail", note["id"], ValueError(note["content_gap"]))
            self._refresh_content_coverage()
            self._flush("read_detail", 32)
        self._finish_stage("details")

    def _read_images(self, note: dict[str, Any], runner: Any) -> None:
        if not note.get("images"):
            note["image_status"] = "not_applicable"
            return
        if note.get("image_status") == "complete":
            return
        sources = note.get("image_sources") or []
        rows: list[dict[str, Any]] = []
        note["image_evidence"] = rows
        note["image_status"] = "partial"
        note["evidenceComplete"] = False
        for source in sources[:MAX_IMAGES_PER_NOTE]:
            self._check_stop()
            index = source["index"]
            row = {**source, "status": "failed", "text": "", "visual_description": "", "uncertain_regions": []}
            rows.append(row)
            self._emit("info", "图片内容识别", f"正在读取第 {index}/{note['images']} 张笔记图片。",
                       {"platform": note["platform"], "noteId": note["id"], "imageIndex": index})
            try:
                image = fetch_note_image(source["url"], note["platform"], stop_check=self._check_stop)
                row.update({key: value for key, value in image.items() if key != "data_uri"})
                output = self._agent(runner, "image_reading_agent", {
                    "platform": note["platform"], "note_id": note["id"],
                    "image": {key: row[key] for key in ("index", "sha256", "mime", "bytes", "width", "height")},
                }, f"识别笔记第 {index} 张图片", image_inputs=[image["data_uri"]])
                row.update({key: output[key] for key in ("status", "text", "visual_description", "uncertain_regions")})
                row["confidence"] = output.get("confidence", 0)
                row["call_id"] = getattr(runner, "last_call_id", "")
                if row["uncertain_regions"] or float(row["confidence"]) < .8:
                    row["status"] = "partial"
                if row["status"] not in {"recognized", "no_text"}:
                    row["error"] = "图片部分内容未能可靠识别，需人工核对。"
            except (PipelineStopped, PlatformNeedsAction):
                raise
            except Exception as exc:
                row["status"] = "failed"
                row["error"] = str(redact_external(str(exc)))[:300]
                row["call_id"] = getattr(runner, "last_call_id", "") if row.get("sha256") else ""
            self._refresh_content_coverage()
            self._flush("read_detail", 32)
        complete = sum(row["status"] in {"recognized", "no_text"} for row in rows)
        note["image_status"] = "complete" if complete == note["images"] else "partial"
        if note["image_status"] != "complete":
            note["content_gap"] = f"共 {note['images']} 张图片，{complete} 张完成识别；其余图片未完整读取，需核验。"
            if len(sources) > MAX_IMAGES_PER_NOTE:
                note["content_gap"] += f" 单条笔记最多自动识别 {MAX_IMAGES_PER_NOTE} 张。"
        else:
            note.pop("content_gap", None)

    def _refresh_content_coverage(self) -> None:
        full_text = self._plan["readDetail"]
        targets = (self._content_targets or self._notes) if full_text else self._notes
        attempted = [n for n in targets if n.get("detailAttempted") or n.get("detailFetched") or n.get("read_error")]
        excluded = [n for n in targets if self._verified_time_exclusion(n)]
        missing = [n for n in attempted if self._missing_content(n) and not self._verified_time_exclusion(n)]
        gaps = [{"platform": n["platform"], "noteId": n["id"],
                 "reason": n.get("read_error") or n.get("content_gap") or "正文尚未取得，不能仅凭标题确认无关。"}
                for n in (missing if full_text else targets) if self._missing_content(n)]
        self._summary["contentCoverage"] = {
            "mode": "full_text" if full_text else "summary", "requested": len(targets) if full_text else 0,
            "attempted": len(attempted), "fetched": sum(bool(n.get("detailFetched")) for n in targets),
            "complete": sum(bool(n.get("detailFetched")) and not self._missing_content(n) and not self._verified_time_exclusion(n) for n in targets),
            "excluded": len(excluded),
            "missing": len(missing) if full_text else len(gaps),
            "failed": sum(bool(n.get("read_error")) for n in targets),
            "pending": len(targets) - len(attempted) if full_text else 0, "gaps": gaps,
            "images": {"total": sum(n.get("images", 0) for n in targets),
                       "attempted": sum(len(n.get("image_evidence") or []) for n in targets),
                       "complete": sum(row.get("status") in {"recognized", "no_text"}
                                       for n in targets for row in n.get("image_evidence") or []),
                       "failed": sum(row.get("status") == "failed"
                                     for n in targets for row in n.get("image_evidence") or [])},
        }

    @staticmethod
    def _verified_time_exclusion(note: dict[str, Any]) -> bool:
        return bool(note.get("time_filter_excluded")) and (note.get("timeFilterCheck") or {}).get("status") == "out_of_range"

    def _report_sources(self) -> dict[str, Any]:
        # Zero leads must still have an inspectable source trail. Bound report
        # detail separately from full aggregate coverage and disclose the limit.
        examined = self._content_targets if self._plan["readDetail"] else self._notes
        selected = examined[:20]
        return {"sourceCoverage": {
                    "notes": len(self._notes),
                    "relevance": dict(Counter((n.get("relevance") or {}).get("decision", "pending") for n in self._notes)),
                    "content": copy.deepcopy(self._summary.get("contentCoverage") or {}),
                    "comments": copy.deepcopy(self._stage_coverage["comments"]),
                    "sourceEvidenceIncluded": len(selected), "sourceEvidenceTotal": len(examined),
                    "sourceEvidenceLimit": 20},
                "sourceEvidence": [{**self._model_note(self._note_brief(n)),
                    "relevance": n.get("relevance") or {},
                    "contentGap": n.get("read_error") or n.get("content_gap") or "",
                    "timeFilterCheck": n.get("timeFilterCheck") or {},
                    "commentsCollected": n.get("scanned_comments_count", 0),
                    "commentCoverage": n.get("commentCoverage") or {}}
                    for n in selected]}

    @staticmethod
    def _collection_rank(note: dict[str, Any]) -> int:
        text = note_content(note)
        priority = (note.get("relevance") or {}).get("collection_priority", "normal")
        if any(term in text for term in ("不接受外包", "不可能外包", "只接受坐班", "不接受异地，外包")):
            priority = "low"
            note["collection_priority_reason"] = "正文明确为坐班/拒绝外包；保留独立评论判断，但降低扫描顺序。"
        note["collection_priority"] = priority
        return {"high": 0, "normal": 1, "low": 2}.get(priority, 1)

    def _collect_comments(self, notes: list[dict[str, Any]], budget: int) -> None:
        self._start_stage("comments", len(notes))
        if notes and budget <= 0:
            self._stage_coverage["comments"].update(status="skipped", unscanned=len(notes),
                reason="已达到确认的评论总上限，本轮未扫描评论。")
            self._publish_coverage()
            return
        seen = set()
        for note in notes:
            self._check_stop()
            if len(self._comments) >= budget:
                self._summary["endReasons"].append("comment_quota_reached")
                self._stage_coverage["comments"]["reason"] = "已达到确认的评论总上限，其余来源未扫描评论。"
                break
            cursor, cursors, count = "", set(), 0
            complete = True
            note["commentCoverage"] = {"status": "running", "requests": 0, "collected": 0,
                                       "limit": self._plan["maxCommentsPerNote"]}
            while count < self._plan["maxCommentsPerNote"] and len(self._comments) < budget:
                self._check_stop()
                try:
                    note["commentCoverage"]["requests"] += 1
                    raw = self._read(self._adapter_for(note["platform"]).comments, note["id"], cursor=cursor)
                    data = raw.get("data") or raw
                    items = data.get("comments") or []
                    added = 0
                    for raw_comment in items:
                        self._check_stop()
                        comment = self._comment_brief(raw_comment, note, self._plan)
                        identity = (note["platform"], note["id"], comment["comment_id"])
                        if identity in seen or comment["comment_id"] in self._seen_comment_ids or f"{note['platform']}:{note['id']}:{comment['comment_id']}" in self._seen_comment_ids:
                            continue
                        seen.add(identity)
                        self._comments.append(comment)
                        count += 1
                        added += 1
                        if self.storage and self._task_id:
                            self.storage.save_comment(self._task_id, note["id"], {
                                **raw_comment, "id": comment["comment_id"], "platform": note["platform"],
                                "source_comment_id": comment["source_comment_id"],
                                "id_is_synthetic": comment["id_is_synthetic"],
                            })
                        if count >= self._plan["maxCommentsPerNote"] or len(self._comments) >= budget:
                            break
                    note["scanned_comments_count"] = count
                    note["commentCoverage"]["collected"] = count
                    self._flush("comments", 60)
                    next_cursor = str(data.get("cursor") or "")
                    if count >= self._plan["maxCommentsPerNote"] or len(self._comments) >= budget:
                        note["commentCoverage"].update(status="limited", reason=(
                            "已达到确认的单篇评论上限。" if count >= self._plan["maxCommentsPerNote"] else "已达到确认的评论总上限。"))
                        break
                    if not items or not added or not data.get("has_more") or not next_cursor or next_cursor in cursors:
                        note["commentCoverage"].update(status="completed", reason="本次评论读取未返回更多可用结果。")
                        break
                    cursors.add(next_cursor)
                    cursor = next_cursor
                except Exception as exc:
                    complete = False
                    note["comment_error"] = str(redact_external(str(exc)))[:300]
                    note["commentCoverage"].update(status="failed", reason=note["comment_error"])
                    self._failure("comments", note["id"], exc)
                    break
            if complete:
                if note["commentCoverage"]["status"] == "running":
                    note["commentCoverage"].update(status="limited", reason=(
                        "已达到确认的单篇评论上限。" if count >= self._plan["maxCommentsPerNote"] else "已达到确认的评论总上限。"))
                self._stage_coverage["comments"]["completed"] += 1
            self._flush("comments", 60)
        self._stage_coverage["comments"]["unscanned"] = sum("commentCoverage" not in n for n in notes)
        self._stage_coverage["comments"]["limited"] = sum((n.get("commentCoverage") or {}).get("status") == "limited" for n in notes)
        if self._stage_coverage["comments"]["limited"]:
            self._stage_coverage["comments"].setdefault("reason", "部分来源已达到确认的评论采集上限，结果仅覆盖已读取的评论。")
        self._finish_stage("comments")

    def _identify(self, runner: Any, selected: list[dict[str, Any]]) -> None:
        candidates = [*self._comments, *self._note_author_candidates(selected, self._plan)]
        self._stage_coverage["qa"]["total"] = len(candidates)
        self._start_stage("lead_signal", len(candidates))
        for chunk in self._chunks(candidates, 5):
            call_ids = {}
            decisions = {candidate["candidate_id"]: {"decision": "ignore", "classification": "irrelevant",
                         "score": 0, "reason": "原文命中用户确认的排除词", "evidence": [candidate.get("content", "")]}
                         for candidate in chunk if any(term.casefold() in str(candidate.get("content", "")).casefold()
                                                       for term in self._plan["excludeKeywords"])}
            batch_error = None
            try:
                for attempt in range(2):
                    missing = [item for item in chunk if item["candidate_id"] not in decisions]
                    if not missing:
                        break
                    output = self._agent(runner, "lead_signal_agent", {"plan": self._plan,
                        "leadCandidates": [self._model_candidate(item) for item in missing],
                        "noteContexts": self._note_contexts(missing),
                        "reviewMode": "missing_batch" if attempt else "initial"}, "分类需求与信息信号")
                    parsed = self._lead_decisions(output.get("leads") or [], missing, "retry" if attempt else "initial")
                    decisions.update(parsed)
                    call_ids.update({key: getattr(runner, "last_call_id", "") for key in parsed})
            except Exception as exc:
                batch_error = exc
            for candidate in chunk:
                if batch_error and candidate["candidate_id"] not in decisions:
                    continue
                decision = decisions.get(candidate["candidate_id"])
                if decision is None:
                    self._summary["failures"].append({"stage": "lead_signal", "source": candidate["candidate_id"], "error": "缺少模型判断"})
                    self._stage_error("lead_signal", "部分候选缺少模型判断，待人工复核。")
                    decision = {"decision": "missing_decision", "classification": "uncertain", "score": 0, "reason": "缺少模型判断，待人工复核"}
                else:
                    self._stage_coverage["lead_signal"]["completed"] += 1
                original_decision = copy.deepcopy(decision)
                decision = apply_offering_decision(candidate, decision, self._plan)
                decision_type = str(decision.get("decision") or "ignore")
                self._save_audit(self._task_id, [{"platform": candidate["platform"], "stage": "lead_signal", "entity_type": "note" if candidate["lead_type"] == "note_author" else "comment",
                    "entity_id": candidate["candidate_id"], "note_id": candidate["note_id"], "decision": decision_type,
                    "source_keyword": (candidate.get("note") or {}).get("source_keyword", ""),
                    "call_id": call_ids.get(candidate["candidate_id"], ""),
                    "original_decision": original_decision, "final_decision": decision,
                    "rule_version": SERVICE_POLICY_VERSION if self._plan.get("offeringId") == "agent_custom_development" else "general",
                    "transformation_reason": decision.get("intent_reason", "") if decision != original_decision else "",
                    "reason": decision.get("intent_reason") or decision.get("reason", ""),
                    "evidence": decision.get("evidence", []), "score": self._score(decision.get("score"))}])
                lead = self._lead_from_decision(candidate, decision, self._plan)
                lead["original_model_decision"] = original_decision
                lead["rule_decision"] = decision
                lead["call_id"] = call_ids.get(candidate["candidate_id"], "")
                self._leads.append(lead)
            self._flush("lead_signal", 75)
            if batch_error:
                raise batch_error
        self._leads.sort(key=lambda x: x["score"], reverse=True)
        self._finish_stage("lead_signal")

    @staticmethod
    def _score(value: Any) -> int:
        try:
            return max(0, min(100, int(value or 0)))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _lead_from_decision(self, candidate: dict[str, Any], decision: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        decision = apply_offering_decision(candidate, decision, settings)
        note, source = candidate["note"], str(candidate.get("content") or "")
        evidence_value = decision.get("evidence") or []
        parts = evidence_value if isinstance(evidence_value, list) else [str(evidence_value)]
        # The model sees redacted text; verify every quotation against that same source.
        valid = [str(part) for part in parts if isinstance(part, str) and part.strip() and part in str(redact_external(source))]
        complete = bool(parts and len(valid) == len(parts) and candidate.get("evidenceComplete"))
        classification = str(decision.get("classification") or "uncertain")
        if classification not in {"demand", "information", "promotion", "uncertain", "irrelevant"}:
            classification = "uncertain"
        score = self._score(decision.get("score"))
        lead_id = candidate["candidate_id"] if candidate["lead_type"] == "note_author" else f"{candidate['note_id']}:{candidate['comment_id']}"
        if candidate["platform"] != "xhs" and not lead_id.startswith(candidate["platform"] + ":"):
            lead_id = candidate["platform"] + ":" + lead_id
        qualified = decision.get("decision") == "lead" and score >= settings["minScore"] and complete
        if settings.get("offeringId") == "agent_custom_development":
            qualified = qualified and classification == "demand" and complete and decision.get("demand_origin") == "self"
        return {"platform": candidate["platform"], "lead_id": lead_id, "lead_type": candidate["lead_type"],
            "comment_id": candidate["comment_id"], "source_comment_id": candidate["source_comment_id"],
            "id_is_synthetic": candidate["id_is_synthetic"],
            "note_id": candidate["note_id"], "post_id": candidate["post_id"],
            "user_id": candidate["user_id"], "target_user_id": candidate["user_id"], "nickname": candidate["nickname"],
            "profile_url": candidate["profile_url"], "content": source, "evidence": "；".join(valid),
            "evidenceComplete": complete, "sourceRefs": candidate["sourceRefs"], "collectedAt": candidate["collectedAt"],
            "classification": classification, "preQaQualified": bool(qualified), "qualified": False,
            "score": score, "priority": "A" if score >= 85 else "B" if score >= 70 else "C",
            **{key: decision.get(key) for key in ("intent_role", "service_fit", "demand_origin",
                                                "customization_evidence", "commissioning_evidence", "intent_reason")},
            "confidence": decision.get("confidence", 0), "demand_summary": decision.get("demand_summary", ""),
            "reason": decision.get("reason", ""), "risk": decision.get("risk", ""), "stage": decision.get("stage", "待人工核验"),
            **inquiry_review(candidate, decision),
            "note_title": note.get("title", ""), "note_url": note.get("url", ""), "ip": candidate.get("ip", ""),
            "qa_status": "pending", "qa_issues": [], "human_review_status": "pending", "feedback": "待人工判断",
            "review_required_reason": "" if complete else "原文证据不足或引用未通过逐字校验", "source_comment": candidate}

    def _review_leads(self, runner: Any) -> None:
        self._start_stage("qa", len(self._leads))
        self._qa = {"approved": False, "issues": [], "review_requests": [], "reviews": [],
                    "batchCount": 0, "reviewedLeadsCount": 0, "expectedLeadsCount": len(self._leads)}
        all_approved = True
        for chunk in self._chunks(self._leads, 5):
            qa = self._agent(runner, "critic_qa_agent", {"plan": self._plan,
                "leads": [self._model_lead(lead) for lead in chunk],
                "noteContexts": self._note_contexts([lead["source_comment"] for lead in chunk])}, "证据质检")
            self._qa["batchCount"] += 1
            rows = self._qa_reviews(qa, chunk)
            missing = [lead for lead in chunk if lead["lead_id"] not in rows]
            if missing:
                retry = self._agent(runner, "critic_qa_agent", {"plan": self._plan,
                    "leads": [self._model_lead(lead) for lead in missing],
                    "noteContexts": self._note_contexts([lead["source_comment"] for lead in missing]),
                    "reviewMode": "missing_batch"}, "补齐缺失的逐条质检")
                self._qa["batchCount"] += 1
                rows.update(self._qa_reviews(retry, missing))
                qa["issues"] = [*qa.get("issues", []), *retry.get("issues", [])]
                qa["review_requests"] = [*qa.get("review_requests", []), *retry.get("review_requests", [])]
            qa["reviews"] = list(rows.values())
            reviewed = len(rows)
            if reviewed != len(chunk):
                error = "部分候选缺少逐条质检结果，未将缺失项视为通过。"
                self._stage_error("qa", error)
                self._summary["failures"].append({"stage": "qa", "error": error})
            self._apply_qa(chunk, qa)
            # Global/plan errors remain aggregate failures without falsely
            # assigning them to every individually approved candidate.
            batch_approved = all(lead["qa_status"] == "approved" for lead in chunk) and not any(
                isinstance(issue, dict) and issue.get("level") == "error"
                for issue in qa.get("issues", []))
            all_approved = all_approved and batch_approved
            self._qa["issues"].extend(qa.get("issues") or [])
            self._qa["review_requests"].extend(qa.get("review_requests") or [])
            self._qa["reviews"].extend(qa.get("reviews") or [])
            self._qa["reviewedLeadsCount"] += reviewed
            self._stage_coverage["qa"]["completed"] += reviewed
            self._qa["approved"] = all_approved and self._qa["reviewedLeadsCount"] == len(self._leads)
            self._flush("qa", 85)
        self._finish_stage("qa")

    @staticmethod
    def _qa_reviews(qa: dict[str, Any], leads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        allowed = {lead["lead_id"] for lead in leads}
        rows = [row for row in qa.get("reviews", []) if isinstance(row, dict)]
        counts = Counter(str(row.get("target_id")) for row in rows)
        return {str(row["target_id"]): row for row in rows
                if str(row.get("target_id")) in allowed and counts[str(row["target_id"])] == 1
                and row.get("status") in {"approved", "needs_review", "rejected"}}

    def _apply_qa(self, leads: list[dict[str, Any]], qa: dict[str, Any]) -> None:
        reviews = {str(x.get("target_id")): x for x in qa.get("reviews", []) if isinstance(x, dict)}
        for lead in leads:
            issues = [x for x in qa.get("issues", []) if isinstance(x, dict) and str(x.get("target_id")) == lead["lead_id"]]
            lead["qa_issues"] = issues
            review = reviews.get(lead["lead_id"], {})
            status = review.get("status")
            lead["qa_status"] = "rejected" if any(x.get("level") == "error" for x in issues) else (
                "needs_review" if issues or not lead["evidenceComplete"] else
                status if status in {"approved", "needs_review", "rejected"} else "needs_review")
            lead["qa_review_requests"] = [x for x in qa.get("review_requests", [])
                if isinstance(x, dict) and str(x.get("target_id")) == lead["lead_id"]]
            lead["business_review_required"] = bool(lead["qa_review_requests"] or lead.get("classification") == "uncertain")
            lead["qa_reason"] = review.get("reason") or ("逐条质检结果缺失，不能视为通过" if not review else "")
            if issues:
                lead["review_required_reason"] = "；".join(str(x.get("message", "")) for x in issues)
            # A model's initial qualification is not a verified result. Preserve
            # it separately; missing or disputed QA must not enter qualified totals.
            lead["qualified"] = bool(lead.get("preQaQualified")) and lead["qa_status"] == "approved"
            if lead["preQaQualified"] and not lead["qualified"]:
                lead["qualification_reason"] = "初步研判符合，但证据质检尚未通过：" + (
                    lead.get("qa_reason") or lead.get("review_required_reason") or "需要逐条复核")

    def _comment_brief(self, comment: dict[str, Any], note: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        platform = note.get("platform", "xhs")
        user = comment.get("user_info") or {}
        content = str(comment.get("content") or "")
        user_id = str(user.get("sec_user_id") or user.get("user_id") or "")
        supplied_id = str((comment.get("source_comment_id") if "source_comment_id" in comment else comment.get("id") or comment.get("comment_id")) or "")
        if comment.get("id_is_synthetic"):
            supplied_id = ""
        comment_id = supplied_id
        if not comment_id:
            timestamp = comment.get("create_time") or comment.get("createTime")
            # An anonymous repeated message is not proof of the same comment.
            # Only identifiable author/time pairs can share a fallback key.
            identity = json.dumps([platform, str(note["id"]), user_id, timestamp, content], ensure_ascii=False)
            suffix = hashlib.sha256(identity.encode()).hexdigest() if user_id and timestamp else uuid.uuid4().hex
            comment_id = "missing-" + suffix
        return {"platform": platform, "candidate_id": f"{platform}:{note['id']}:{comment_id}",
                "lead_type": "comment_lead", "comment_id": comment_id, "note_id": note["id"],
                "source_comment_id": supplied_id, "id_is_synthetic": not bool(supplied_id),
                "post_id": note.get("post_id") or note["id"], "user_id": user_id,
                "nickname": user.get("nickname") or user.get("nick_name") or "", "profile_url": self._profile_url(platform, user_id),
                "content": content, "ip": comment.get("ip_location", ""), "note": self._note_brief(note),
                "collectedAt": time.time(), "evidenceComplete": bool(supplied_id and content and note.get("url")),
                "sourceRefs": [{"platform": platform, "noteId": note["id"], "commentId": supplied_id, "url": note.get("url", "")} ]}

    def _note_author_candidates(self, notes: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = []
        for note in notes:
            candidate = self._comment_brief({"content": note_content(note),
                 "user_info": {"user_id": note.get("author_user_id", ""), "nickname": note.get("author", "")}}, note, settings)
            candidate.update(candidate_id=("" if note["platform"] == "xhs" else note["platform"] + ":") + "note:" + str(note["id"]),
                lead_type="note_author", comment_id="", source_comment_id="", id_is_synthetic=False,
                evidenceComplete=bool(note.get("evidenceComplete")),
                sourceRefs=note["sourceRefs"], collectedAt=note["collectedAt"])
            candidates.append(candidate)
        return candidates

    def _note_brief(self, note: dict[str, Any]) -> dict[str, Any]:
        return {"platform": note.get("platform", "xhs"), "note_id": note["id"], "post_id": note.get("post_id", note["id"]),
                **{key: note.get(key) for key in ("title", "desc", "tags", "url", "source_keyword", "evidenceComplete", "sourceRefs", "collectedAt",
                                                "images", "image_status", "image_evidence", "content_gap")}}

    @staticmethod
    def _model_note(note: dict[str, Any]) -> dict[str, Any]:
        return {**{key: note.get(key) for key in ("platform", "note_id", "title", "desc", "tags",
                                               "source_keyword", "evidenceComplete", "image_status", "content_gap")},
                "image_evidence": [{key: row.get(key) for key in ("index", "status", "text", "visual_description", "uncertain_regions")}
                                   for row in note.get("image_evidence") or []]}

    @staticmethod
    def _model_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        return {key: candidate.get(key) for key in ("platform", "candidate_id", "note_id", "comment_id",
                                                    "lead_type", "content", "evidenceComplete")}

    def _note_contexts(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        contexts = {}
        for candidate in candidates:
            if candidate.get("lead_type") == "note_author" and not (candidate.get("note") or {}).get("image_evidence"):
                # Author content already contains the full title and body.
                continue
            note = candidate.get("note") or {}
            identity = (candidate.get("platform"), candidate.get("note_id"))
            contexts[identity] = self._model_note(note)
        return list(contexts.values())

    @staticmethod
    def _model_lead(lead: dict[str, Any]) -> dict[str, Any]:
        return {key: lead.get(key) for key in (
            "platform", "lead_id", "lead_type", "note_id", "comment_id", "content", "evidence",
            "evidenceComplete", "classification", "qualified", "score", "confidence", "demand_summary", "reason",
            "risk", "intent_role", "service_fit", "demand_origin", "customization_evidence",
            "commissioning_evidence", "intent_reason", "preQaQualified", "qa_status", "qa_reason",
            "qualification_reason", "business_review_required",
        )}

    def _flush(self, stage: str, percent: int | None) -> None:
        # The confirmed metric orders retained sources and subsequent scanning;
        # it does not alter the keyword/platform collection quotas.
        self._notes.sort(key=lambda note: note.get(self._plan["metric"], 0), reverse=True)
        self._refresh_content_coverage()
        self._summary["notesCollected"] = len(self._notes)
        self._summary["commentsCollected"] = len(self._comments)
        self._summary["unprocessedNotes"] = max(0, self._summary["receivedNotes"] - len(self._notes)
                                               - sum(self._summary["filterCounts"].values()))
        if self.storage and self._task_id:
            for note in self._notes:
                identity = (str(note.get("platform", "xhs")), str(note["id"]))
                fingerprint = self._row_fingerprint(note)
                if self._saved_note_fingerprints.get(identity) != fingerprint:
                    self.storage.save_note(self._task_id, note)
                    self._saved_note_fingerprints[identity] = fingerprint
            for lead in self._leads:
                identity = (str(lead.get("platform", "xhs")), str(lead["lead_id"]))
                fingerprint = self._row_fingerprint(lead)
                if self._saved_lead_fingerprints.get(identity) != fingerprint:
                    self.storage.save_lead(self._task_id, lead)
                    self._saved_lead_fingerprints[identity] = fingerprint
        if percent is not None:
            self._last_progress_percent = percent
        self._progress({"stage": stage, "percent": self._last_progress_percent,
                        "current": len(self._notes), "total": self._summary["effectiveMaxNotes"]})
        if self._snapshot:
            self._snapshot(copy.deepcopy(self._result()))

    @staticmethod
    def _row_fingerprint(row: dict[str, Any]) -> str:
        serialized = canonical_json(row)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _result(self) -> dict[str, Any]:
        trace = self.storage.list_agent_runs(self._task_id) if self.storage and self._task_id else []
        return {"taskId": self._task_id, "status": self._status, "errorText": self._error,
                "keyword": "、".join(self._plan["keywords"]), "demandDescription": self._plan["searchGoal"],
                "confirmedPlan": self._plan, "platforms": self._plan["platforms"], "runId": self._active_run_id,
                "runType": self._active_run_type, "count": len(self._notes), "notes": self._notes,
                "metric": self._plan["metric"], "metricLabel": METRIC_LABELS[self._plan["metric"]],
                "scannedCommentsCount": len(self._comments), "collectionSummary": self._summary,
                "stageCoverage": self._stage_coverage,
                "leads": {"keyword": "、".join(self._plan["keywords"]), "scannedNotesCount": len(self._notes),
                          "totalCommentsScanned": len(self._comments), "leadsCount": len(self._leads),
                          "qualifiedCount": sum(1 for x in self._leads if x.get("qualified")),
                          "minScore": self._plan["minScore"], "leads": self._leads},
                "report": self._report, "qa": self._qa, "agentTrace": trace, "auditTrail": self._audit_from_trace(trace),
                "taskSummary": {"task_summary": f"已采集 {len(self._notes)} 条内容、{len(self._comments)} 条评论；候选 {len(self._leads)} 条，需人工核验。"},
                "harness": {"mode": "multi-agent-llm", "runId": self._active_run_id},
                "safety": {"mode": "read-only", "actions": "采集只读；触达通过独立人工确认动作执行。"}}

    def _adapter_for(self, platform: str) -> Any:
        if hasattr(self.adapter, "adapter_for"):
            return self.adapter.adapter_for(platform)
        return self.adapter




    def _audit_stage(
        self,
        task_id: int | None,
        *,
        stage: str,
        agent_key: str,
        decision: str,
        reason: str,
        evidence: Any = None,
        confidence: Any = None,
        risk: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._save_audit(task_id, [self._stage_audit_item(
            stage=stage,
            agent_key=agent_key,
            decision=decision,
            reason=reason,
            evidence=evidence,
            confidence=confidence,
            risk=risk,
            payload=payload,
        )])


    def _stage_audit_item(
        self,
        *,
        stage: str,
        agent_key: str,
        decision: str,
        reason: str,
        evidence: Any = None,
        confidence: Any = None,
        risk: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "stage": stage,
            "entity_type": "agent",
            "entity_id": agent_key,
            "decision": decision,
            "reason": reason,
            "evidence": evidence if evidence is not None else [],
            "confidence": confidence,
            "risk": risk,
            "payload": {"agent_key": agent_key, **(payload or {})},
        }


    def _save_audit(self, task_id: int | None, items: list[dict[str, Any]]) -> None:
        if self.storage and task_id and items:
            prepared = []
            default_platform = self._default_audit_platform()
            for item in items:
                normalized = dict(item)
                normalized.setdefault("run_id", self._active_run_id)
                normalized.setdefault("run_type", self._active_run_type or "main")
                normalized.setdefault("platform", default_platform)
                payload = normalized.get("payload")
                if isinstance(payload, dict):
                    payload.setdefault("run_id", normalized.get("run_id"))
                    payload.setdefault("run_type", normalized.get("run_type"))
                prepared.append(normalized)
            self.storage.save_audit_decisions(task_id, prepared)


    def _default_audit_platform(self) -> str:
        platforms = [str(item) for item in (self._active_platforms or []) if str(item)]
        if len(platforms) == 1:
            return platforms[0]
        if len(platforms) > 1:
            return "multi"
        return "xhs"


    def _chunks(self, items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
        for index in range(0, len(items), max(1, size)):
            yield items[index:index + size]


    def _note_decisions(self, items: list[dict[str, Any]], coverage_status: str) -> dict[str, dict[str, Any]]:
        decisions: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict) or item.get("decision") not in {"keep", "drop", "needs_content"}:
                continue
            note_id = str(item.get("note_id") or item.get("id") or "").strip()
            if not note_id:
                continue
            normalized = dict(item)
            normalized["coverage_status"] = coverage_status
            decisions[note_id] = normalized
        return decisions


    def _lead_decisions(
        self,
        items: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        coverage_status: str,
    ) -> dict[str, dict[str, Any]]:
        allowed = {item["candidate_id"] for item in candidates}
        by_pair = {(str(item.get("note_id")), str(item.get("comment_id"))): item["candidate_id"]
                   for item in candidates if item.get("comment_id")}
        by_comment = {str(item.get("comment_id")): item["candidate_id"] for item in candidates
                      if item.get("comment_id") and sum(c.get("comment_id") == item.get("comment_id") for c in candidates) == 1}
        by_note_author = {
            str(item.get("note_id")): item["candidate_id"]
            for item in candidates
            if item.get("lead_type") == "note_author"
        }
        decisions: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict) or item.get("decision") not in {"lead", "ignore", "below_threshold"}:
                continue
            candidate_id = str(item.get("candidate_id") or item.get("lead_id") or "").strip()
            if candidate_id not in allowed and item.get("comment_id"):
                candidate_id = by_pair.get((str(item.get("note_id")), str(item["comment_id"])), "") or by_comment.get(str(item["comment_id"]), "")
            if not candidate_id and str(item.get("lead_type") or "") == "note_author":
                candidate_id = by_note_author.get(str(item.get("note_id")), "")
            if not candidate_id and not item.get("comment_id") and item.get("note_id"):
                candidate_id = by_note_author.get(str(item.get("note_id")), "")
            if candidate_id not in allowed:
                continue
            normalized = dict(item)
            normalized["candidate_id"] = candidate_id
            normalized.setdefault("lead_type", "note_author" if candidate_id.startswith("note:") else "comment_lead")
            normalized["coverage_status"] = coverage_status
            decisions[candidate_id] = normalized
        return decisions




    def _profile_url(self, platform: str, user_id: Any) -> str:
        target = str(user_id or "").strip()
        if not target:
            return ""
        if target.startswith("http://") or target.startswith("https://"):
            return target
        if platform == "douyin":
            return f"https://www.douyin.com/user/{target}"
        return f"https://www.xiaohongshu.com/user/profile/{target}"


    def _audit_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "agent": item.get("agentName") or item.get("agentKey"),
                "message": item.get("purpose") or item.get("decision") or item.get("error") or "Agent 调用",
                "data": item,
            }
            for item in trace[-100:]
        ]
