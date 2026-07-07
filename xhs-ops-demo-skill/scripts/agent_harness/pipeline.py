from __future__ import annotations

import random
import time
from typing import Any

from llm_client import has_llm_key
from lead_pipeline import METRIC_LABELS, extract_note, merge_note, within_time_filter
from product_catalog import BUSINESS_DIRECTIONS, PRODUCTS, product_distribution
from sales_scoring import redact_text

from .evaluator import HarnessEvaluator
from .registry import PromptRegistry
from .runtime import StructuredAgentRunner


class HarnessPipeline:
    def __init__(
        self,
        adapter: Any,
        config: dict[str, Any],
        storage: Any | None = None,
        runner: StructuredAgentRunner | None = None,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.storage = storage
        self.runner = runner

    def run(
        self,
        payload: dict[str, Any],
        task_id: int | None = None,
        page: int = 1,
        stop_checker: Any | None = None,
        event_callback: Any | None = None,
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        emit = event_callback or (lambda level, agent, message, data=None: None)
        progress = progress_callback or (lambda data: None)
        llm_config = self.config.get("llm") or {}
        if not has_llm_key(llm_config.get("api_key")):
            raise RuntimeError("Harness 模式必须配置 LLM API Key；请在设置中填写 API Key 或设置 LLM_API_KEY/OPENAI_API_KEY。")

        registry = PromptRegistry(self.storage)
        registry.ensure_defaults()
        runner = self.runner or StructuredAgentRunner(self.config, self.storage, task_id, registry)
        evaluator = HarnessEvaluator(self.storage)

        emit("info", "登录检查", "检查小红书登录态...")
        self.adapter.ensure_authenticated()
        emit("success", "登录检查", "小红书登录态有效。")

        settings = self._settings(payload)
        task_context = {
            "payload": payload,
            "demandDescription": settings["demandDescription"],
            "userConstraints": settings["userConstraints"],
            "businessDirectionLabel": settings["businessDirectionLabel"],
            "availableProducts": self._product_labels(),
            "safety": self._safety(settings),
        }
        progress({"stage": "orchestrate", "percent": 4, "current": 0, "total": 1})

        emit("info", "总控编排 Agent", "理解任务并拆分多 Agent 执行计划...")
        chief = runner.run("chief_orchestrator", task_context, purpose="任务理解与阶段编排")
        emit("success", "总控编排 Agent", str(chief.get("decision") or "执行计划已生成。"), chief)

        progress({"stage": "plan", "percent": 8, "current": 0, "total": 1})
        emit("info", "搜索规划 Agent", "生成搜索策略和关键词包...")
        planning = runner.run("planning_agent", {**task_context, "orchestrator": chief}, purpose="搜索策略规划")
        settings["planning"] = planning
        keywords = self._keywords(planning)
        emit("success", "搜索规划 Agent", f"生成 {len(keywords)} 个 LLM 规划搜索词。", {"keywords": keywords, "planning": planning})
        self._audit_planned_keywords(task_id, keywords, planning)

        raw_notes = self._collect_raw_notes(keywords, settings, page, task_id, stop_checker, emit, progress)
        progress({"stage": "relevance", "percent": 32, "current": 0, "total": len(raw_notes)})
        selected = self._judge_relevance(runner, raw_notes, planning, settings, task_id, stop_checker, emit)
        selected = selected[: settings["count"] * max(1, len(keywords))]

        if settings["readDetail"]:
            progress({"stage": "read_detail", "percent": 42, "current": 0, "total": len(selected)})
            selected = self._enrich_notes(selected, settings, stop_checker, emit, progress)
        else:
            emit("info", "工具 Harness", "已按用户设置跳过详情读取。")

        comments: list[dict[str, Any]] = []
        total_comments = 0
        if settings["scanLeads"]:
            progress({"stage": "comments", "percent": 56, "current": 0, "total": len(selected)})
            comments, total_comments = self._collect_comments(selected, settings, task_id, stop_checker, emit, progress)
        else:
            emit("info", "工具 Harness", "已按用户设置跳过评论扫描。")

        progress({"stage": "lead_signal", "percent": 68, "current": 0, "total": len(comments)})
        leads = self._judge_leads(runner, selected, comments, settings, task_id, stop_checker, emit)

        progress({"stage": "product_strategy", "percent": 76, "current": 0, "total": len(leads)})
        leads, distribution = self._apply_product_strategy(runner, leads, selected, settings, emit)

        progress({"stage": "qa", "percent": 84, "current": 0, "total": 1})
        qa = self._run_qa(runner, selected, leads, distribution, settings, emit)

        for idx, note in enumerate(sorted(selected, key=lambda item: item.get(settings["metric"], 0), reverse=True), 1):
            note["rank"] = idx
            if self.storage and task_id:
                self.storage.save_note(task_id, note)
        if self.storage and task_id:
            for lead in leads:
                self.storage.save_lead(task_id, lead)

        progress({"stage": "report", "percent": 90, "current": 1, "total": 1})
        report = self._make_report(runner, selected, leads, distribution, qa, settings, emit)

        progress({"stage": "reflection", "percent": 95, "current": 1, "total": 1})
        reflection = self._reflect_and_evolve(runner, evaluator, task_id, selected, leads, report, qa, emit)

        agent_trace = self.storage.list_agent_runs(task_id) if self.storage and task_id else []
        task_summary = self.storage.get_task_summary(task_id) if self.storage and task_id else reflection
        prompt_summary = registry.summary()
        audit_trail = self._audit_from_trace(agent_trace)
        emit("success", "总控编排 Agent", f"Harness 任务完成：{len(selected)} 篇笔记，{len(leads)} 条 LLM 达标线索。")
        progress({"stage": "done", "percent": 100, "current": 1, "total": 1})
        return {
            "keyword": "、".join(keywords),
            "demandDescription": settings["demandDescription"],
            "businessDirection": settings["businessDirection"],
            "businessDirectionLabel": settings["businessDirectionLabel"],
            "industry": settings["industry"],
            "region": settings["region"],
            "metric": settings["metric"],
            "metricLabel": METRIC_LABELS[settings["metric"]],
            "count": len(selected),
            "notes": selected,
            "leads": {
                "keyword": "、".join(keywords),
                "scannedNotesCount": len(selected),
                "totalCommentsScanned": total_comments,
                "leadsCount": len(leads),
                "minScore": settings["minScore"],
                "leads": leads,
                "productDistribution": distribution,
            },
            "report": report,
            "qa": qa,
            "taskSummary": task_summary,
            "evolutionSummary": reflection.get("evolution_summary") or reflection.get("decision") or "",
            "promptVersionSummary": prompt_summary,
            "agentTrace": agent_trace,
            "auditTrail": audit_trail,
            "safety": self._safety(settings),
            "harness": {"mode": "multi-agent-llm", "reviewPolicy": chief.get("review_policy", {})},
        }

    def _settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        xhs_config = self.config.get("xhs") or {}
        sales_config = self.config.get("sales") or {}
        direction = str(payload.get("businessDirection") or sales_config.get("default_business_direction") or "cloud_ai")
        direction_label = BUSINESS_DIRECTIONS.get(direction, BUSINESS_DIRECTIONS["cloud_ai"])["label"]
        demand_description = str(
            payload.get("demandDescription")
            or payload.get("demand_description")
            or payload.get("businessDirectionLabel")
            or direction_label
            or "寻找可人工跟进的线索"
        ).strip()
        metric = str(payload.get("metric") or "engagement")
        if metric not in METRIC_LABELS:
            metric = "engagement"
        count = min(max(int(payload.get("count", 10)), 1), int(xhs_config.get("max_count", 20)))
        min_score = min(max(int(payload.get("minScore", sales_config.get("default_min_score", 60))), 0), 100)
        delay_min = min(max(float(payload.get("delayMin", xhs_config.get("read_delay_min", 7.0))), 0.0), 120.0)
        delay_max = min(max(float(payload.get("delayMax", xhs_config.get("read_delay_max", 60.0))), delay_min), 120.0)
        return {
            "businessDirection": direction,
            "businessDirectionLabel": direction_label,
            "demandDescription": demand_description,
            "industry": str(payload.get("industry") or "").strip(),
            "region": str(payload.get("region") or "").strip(),
            "keyword": str(payload.get("keyword") or "").strip(),
            "metric": metric,
            "count": count,
            "minScore": min_score,
            "timeFilter": str(payload.get("timeFilter") or "all"),
            "readDetail": bool(payload.get("readDetail", True)),
            "scanLeads": bool(payload.get("scanLeads", True)),
            "delayMin": delay_min,
            "delayMax": delay_max,
            "redactUser": bool(sales_config.get("redact_user", False)),
            "userConstraints": {
                "industry": str(payload.get("industry") or "").strip(),
                "region": str(payload.get("region") or "").strip(),
                "seedKeywords": str(payload.get("keyword") or "").strip(),
                "metric": metric,
                "count": count,
                "minScore": min_score,
                "timeFilter": str(payload.get("timeFilter") or "all"),
                "readDetail": bool(payload.get("readDetail", True)),
                "scanLeads": bool(payload.get("scanLeads", True)),
                "legacyBusinessDirection": direction,
                "legacyBusinessDirectionLabel": direction_label,
            },
        }

    def _keywords(self, planning: dict[str, Any]) -> list[str]:
        keywords = [str(item).strip() for item in planning.get("keywords") or [] if str(item).strip()]
        seen: set[str] = set()
        result = []
        for keyword in keywords:
            if keyword not in seen:
                seen.add(keyword)
                result.append(keyword)
        if not result:
            raise RuntimeError("搜索规划 Agent 未返回可执行关键词，Harness 已停止，避免退回规则伪装 AI。")
        return result[:12]

    def _collect_raw_notes(
        self,
        keywords: list[str],
        settings: dict[str, Any],
        page: int,
        task_id: int | None,
        stop_checker: Any | None,
        emit: Any,
        progress: Any,
    ) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = time.time()
        for index, keyword in enumerate(keywords, 1):
            if stop_checker and stop_checker():
                break
            emit("info", "工具 Harness", f"只读搜索「{keyword}」第 {page} 页...")
            raw = self.adapter.search(keyword, page=page)
            items = (raw.get("data") or {}).get("items") or []
            audit_items = []
            for item in items:
                note = extract_note(item)
                if not note:
                    audit_items.append({
                        "stage": "search",
                        "entity_type": "note",
                        "entity_id": str(item.get("id") or ""),
                        "source_keyword": keyword,
                        "decision": "parse_failed",
                        "reason": "搜索结果无法解析为有效笔记。",
                        "payload": {"rawId": item.get("id"), "modelType": item.get("model_type")},
                    })
                    continue
                if note["id"] in seen:
                    audit_items.append({
                        "stage": "search",
                        "entity_type": "note",
                        "entity_id": note["id"],
                        "note_id": note["id"],
                        "source_keyword": keyword,
                        "decision": "dropped_duplicate",
                        "reason": "同一任务中该笔记已由其他关键词命中。",
                        "payload": self._note_brief(note),
                    })
                    continue
                if not within_time_filter(note, settings["timeFilter"], now):
                    audit_items.append({
                        "stage": "search",
                        "entity_type": "note",
                        "entity_id": note["id"],
                        "note_id": note["id"],
                        "source_keyword": keyword,
                        "decision": "dropped_time_filter",
                        "reason": f"发布时间不满足时效过滤：{settings['timeFilter']}。",
                        "payload": self._note_brief(note),
                    })
                    continue
                note["source_keyword"] = keyword
                seen.add(note["id"])
                notes.append(note)
                audit_items.append({
                    "stage": "search",
                    "entity_type": "note",
                    "entity_id": note["id"],
                    "note_id": note["id"],
                    "source_keyword": keyword,
                    "decision": "collected",
                    "reason": "搜索返回并通过基础去重与时效过滤，进入 LLM 相关性判断池。",
                    "payload": self._note_brief(note),
                })
            self._save_audit(task_id, audit_items)
            progress({"stage": "search", "percent": 10 + int(18 * index / max(1, len(keywords))), "current": index, "total": len(keywords)})
            emit("success", "工具 Harness", f"搜索「{keyword}」返回 {len(items)} 条，进入 LLM 相关性判断池。")
        notes.sort(key=lambda note: note.get(settings["metric"], 0), reverse=True)
        return notes[: settings["count"] * max(2, len(keywords))]

    def _judge_relevance(
        self,
        runner: StructuredAgentRunner,
        notes: list[dict[str, Any]],
        planning: dict[str, Any],
        settings: dict[str, Any],
        task_id: int | None,
        stop_checker: Any | None,
        emit: Any,
    ) -> list[dict[str, Any]]:
        if not notes:
            return []
        emit("info", "相关性判断 Agent", f"LLM 批量判断 {len(notes)} 篇候选笔记相关性...")
        payload = {
            "demandDescription": settings["demandDescription"],
            "businessDirection": settings["businessDirectionLabel"],
            "strategy": self._strategy_context(settings, planning),
            "hypotheses": planning.get("hypotheses", []),
            "notes": [self._note_brief(note) for note in notes],
        }
        result = runner.run("relevance_agent", payload, purpose="批量笔记相关性判断")
        decisions = {str(item.get("note_id")): item for item in result.get("notes") or []}
        selected = []
        audit_items = []
        for note in notes:
            decision = decisions.get(str(note.get("id"))) or {}
            note["relevance"] = decision
            decision_value = str(decision.get("decision", "")).lower()
            final_decision = decision_value if decision_value in {"keep", "drop"} else "missing_decision"
            if final_decision == "keep":
                selected.append(note)
            audit_items.append({
                "stage": "relevance",
                "entity_type": "note",
                "entity_id": note.get("id"),
                "note_id": note.get("id"),
                "source_keyword": note.get("source_keyword", ""),
                "decision": final_decision,
                "reason": decision.get("reason") or ("相关性 Agent 未返回该笔记判断。" if final_decision == "missing_decision" else ""),
                "evidence": decision.get("evidence") or [],
                "confidence": decision.get("confidence"),
                "payload": {"note": self._note_brief(note), "decision": decision},
            })
        self._save_audit(task_id, audit_items)
        review_targets = [
            note for note in selected
            if float((note.get("relevance") or {}).get("confidence") or 0) < 0.6
        ][:5]
        if review_targets:
            emit("info", "相关性判断 Agent", f"复审 {len(review_targets)} 篇低置信相关笔记...")
            for note in review_targets:
                if stop_checker and stop_checker():
                    break
                review = runner.run(
                    "relevance_agent",
                    {"reviewMode": "single", "demandDescription": settings["demandDescription"], "businessDirection": settings["businessDirectionLabel"], "strategy": self._strategy_context(settings, planning), "note": self._note_brief(note)},
                    purpose="自适应相关性复审",
                )
                decision = (review.get("notes") or [{}])[0]
                note["relevance_review"] = decision
                if decision and str(decision.get("decision", "")).lower() == "drop":
                    self._save_audit(task_id, [{
                        "stage": "relevance",
                        "entity_type": "note",
                        "entity_id": note.get("id"),
                        "note_id": note.get("id"),
                        "source_keyword": note.get("source_keyword", ""),
                        "decision": "drop",
                        "reason": "低置信复审后丢弃：" + str(decision.get("reason") or ""),
                        "evidence": decision.get("evidence") or [],
                        "confidence": decision.get("confidence"),
                        "payload": {"note": self._note_brief(note), "review": decision},
                    }])
                    selected = [item for item in selected if item.get("id") != note.get("id")]
        emit("success", "相关性判断 Agent", f"LLM 保留 {len(selected)} 篇相关笔记。")
        return selected

    def _enrich_notes(
        self,
        notes: list[dict[str, Any]],
        settings: dict[str, Any],
        stop_checker: Any | None,
        emit: Any,
        progress: Any,
    ) -> list[dict[str, Any]]:
        enriched = []
        for idx, note in enumerate(notes):
            if stop_checker and stop_checker():
                break
            if idx > 0 and settings["delayMax"] > 0:
                emit("info", "延迟保护", f"合规延迟后读取详情：{note.get('title', '')[:24]}")
                time.sleep(random.uniform(settings["delayMin"], settings["delayMax"]))
            try:
                emit("info", "工具 Harness", f"只读读取详情 {idx + 1}/{len(notes)}：{note.get('title', '')[:24]}")
                detail_raw = self.adapter.read_note(note["id"])
                detail_items = (detail_raw.get("data") or {}).get("items") or []
                detail = extract_note(detail_items[0]) if detail_items else None
                enriched.append(merge_note(note, detail))
            except Exception as exc:
                note["read_error"] = str(exc)[:300]
                enriched.append(note)
                emit("warning", "工具 Harness", f"读取详情失败：{note.get('title', '')[:24]}", {"error": str(exc)[:300]})
            progress({"stage": "read_detail", "percent": 42 + int(12 * (idx + 1) / max(1, len(notes))), "current": idx + 1, "total": len(notes)})
        return enriched

    def _collect_comments(
        self,
        notes: list[dict[str, Any]],
        settings: dict[str, Any],
        task_id: int | None,
        stop_checker: Any | None,
        emit: Any,
        progress: Any,
    ) -> tuple[list[dict[str, Any]], int]:
        comments = []
        total = 0
        for idx, note in enumerate(notes):
            if stop_checker and stop_checker():
                break
            if idx > 0 and settings["delayMax"] > 0:
                emit("info", "延迟保护", f"合规延迟后读取评论：{note.get('title', '')[:24]}")
                time.sleep(random.uniform(settings["delayMin"], settings["delayMax"]))
            try:
                emit("info", "工具 Harness", f"只读扫描评论 {idx + 1}/{len(notes)}：{note.get('title', '')[:24]}")
                raw = self.adapter.comments(note["id"])
                raw_comments = (raw.get("data") or {}).get("comments") or []
                total += len(raw_comments)
                for comment in raw_comments:
                    normalized = self._comment_brief(comment, note, settings)
                    comments.append(normalized)
                    if self.storage and task_id:
                        self.storage.save_comment(task_id, note["id"], {**comment, "id": normalized["comment_id"]})
            except Exception as exc:
                note["comment_error"] = str(exc)[:300]
                emit("warning", "工具 Harness", f"评论扫描失败：{note.get('title', '')[:24]}", {"error": str(exc)[:300]})
            progress({"stage": "comments", "percent": 56 + int(10 * (idx + 1) / max(1, len(notes))), "current": idx + 1, "total": len(notes)})
        return comments, total

    def _judge_leads(
        self,
        runner: StructuredAgentRunner,
        notes: list[dict[str, Any]],
        comments: list[dict[str, Any]],
        settings: dict[str, Any],
        task_id: int | None,
        stop_checker: Any | None,
        emit: Any,
    ) -> list[dict[str, Any]]:
        if not comments:
            return []
        emit("info", "线索识别 Agent", f"LLM 批量识别 {len(comments)} 条评论销售意图...")
        result = runner.run(
            "lead_signal_agent",
            {
                "demandDescription": settings["demandDescription"],
                "businessDirection": settings["businessDirectionLabel"],
                "strategy": self._strategy_context(settings, settings.get("planning") or {}),
                "notes": [self._note_brief(n) for n in notes],
                "comments": comments,
            },
            purpose="批量线索信号识别",
        )
        by_comment = {str(item.get("comment_id")): item for item in result.get("leads") or []}
        leads = []
        audit_items = []
        for comment in comments:
            decision = by_comment.get(str(comment.get("comment_id"))) or {}
            raw_decision = str(decision.get("decision", "")).lower()
            score = min(max(int(decision.get("score") or 0), 0), 100)
            if raw_decision != "lead":
                audit_items.append(self._lead_audit_item(comment, decision, "ignore", "线索识别 Agent 判断为非商机或未返回该评论。"))
                continue
            if score < settings["minScore"]:
                audit_items.append(self._lead_audit_item(comment, decision, "below_threshold", f"评分 {score} 低于阈值 {settings['minScore']}。"))
                continue
            audit_items.append(self._lead_audit_item(comment, decision, "lead", "达到线索阈值，进入产品策略阶段。"))
            leads.append(self._lead_from_decision(comment, decision, settings))
        self._save_audit(task_id, audit_items)
        review_targets = [
            lead for lead in leads
            if float(lead.get("confidence") or 0) < 0.6 or lead.get("priority") == "A"
        ][:8]
        if review_targets:
            emit("info", "线索识别 Agent", f"自适应复审 {len(review_targets)} 条低置信/高价值线索...")
            for lead in review_targets:
                if stop_checker and stop_checker():
                    break
                review = runner.run(
                    "lead_signal_agent",
                    {
                        "reviewMode": "single",
                        "demandDescription": settings["demandDescription"],
                        "strategy": self._strategy_context(settings, settings.get("planning") or {}),
                        "lead": lead,
                        "sourceComment": lead.get("source_comment", {}),
                    },
                    purpose="自适应线索复审",
                )
                updated = (review.get("leads") or [{}])[0]
                if updated:
                    lead["review"] = updated
                    lead["confidence"] = float(updated.get("confidence") or lead.get("confidence") or 0)
                    lead["risk"] = updated.get("risk") or lead.get("risk")
                    lead["reason"] = updated.get("reason") or lead.get("reason")
                    if str(updated.get("decision", "")).lower() == "ignore":
                        lead["priority"] = "D"
                        lead["score"] = min(int(lead.get("score") or 0), 30)
                        self._save_audit(task_id, [self._lead_audit_item(lead.get("source_comment") or {}, updated, "ignore", "自适应复审后改判为非商机。")])
        leads = [lead for lead in leads if int(lead.get("score") or 0) >= settings["minScore"]]
        leads.sort(key=lambda lead: int(lead.get("score") or 0), reverse=True)
        emit("success", "线索识别 Agent", f"LLM 识别 {len(leads)} 条达标线索。")
        return leads

    def _apply_product_strategy(
        self,
        runner: StructuredAgentRunner,
        leads: list[dict[str, Any]],
        notes: list[dict[str, Any]],
        settings: dict[str, Any],
        emit: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not leads:
            return [], []
        emit("info", "产品策略 Agent", "匹配产品方向并生成低打扰人工话术...")
        result = runner.run(
            "product_strategy_agent",
            {
                "demandDescription": settings["demandDescription"],
                "businessDirection": settings["businessDirectionLabel"],
                "strategy": self._strategy_context(settings, settings.get("planning") or {}),
                "leads": leads,
                "notes": [self._note_brief(n) for n in notes],
                "products": self._product_labels(),
            },
            purpose="产品匹配与人工跟进话术",
        )
        updates = {str(item.get("lead_id")): item for item in result.get("lead_updates") or []}
        for lead in leads:
            update = updates.get(str(lead.get("lead_id"))) or {}
            lead["recommended_products"] = update.get("recommended_products") or lead.get("recommended_products") or ""
            lead["matched_products"] = _normalize_products(update.get("matched_products") or lead.get("matched_products") or [])
            lead["demand_summary"] = update.get("demand_summary") or lead.get("demand_summary") or ""
            lead["outreach_copy"] = update.get("outreach_copy") or lead.get("outreach_copy") or ""
            lead["product_reason"] = update.get("reason") or ""
        distribution = result.get("productDistribution") or product_distribution(leads)
        emit("success", "产品策略 Agent", f"完成 {len(distribution)} 个产品方向匹配。")
        return leads, distribution

    def _run_qa(
        self,
        runner: StructuredAgentRunner,
        notes: list[dict[str, Any]],
        leads: list[dict[str, Any]],
        distribution: list[dict[str, Any]],
        settings: dict[str, Any],
        emit: Any,
    ) -> dict[str, Any]:
        emit("info", "质检风控 Agent", "检查证据一致性、幻觉风险和合规边界...")
        qa = runner.run(
            "critic_qa_agent",
            {
                "demandDescription": settings["demandDescription"],
                "strategy": self._strategy_context(settings, settings.get("planning") or {}),
                "notes": [self._note_brief(n) for n in notes],
                "leads": leads,
                "productDistribution": distribution,
                "safety": self._safety(settings),
            },
            purpose="质量与安全边界检查",
        )
        issues = qa.get("issues") or []
        for issue in issues:
            target_id = str(issue.get("target_id") or "")
            for lead in leads:
                if target_id and target_id == str(lead.get("lead_id")):
                    lead.setdefault("qa_issues", []).append(issue)
        emit("success" if qa.get("approved", True) else "warning", "质检风控 Agent", f"QA 完成，发现 {len(issues)} 个问题。", qa)
        return qa

    def _make_report(
        self,
        runner: StructuredAgentRunner,
        notes: list[dict[str, Any]],
        leads: list[dict[str, Any]],
        distribution: list[dict[str, Any]],
        qa: dict[str, Any],
        settings: dict[str, Any],
        emit: Any,
    ) -> dict[str, Any]:
        emit("info", "报告 Agent", "生成 LLM 获客报告...")
        report = runner.run(
            "report_agent",
            {
                "demandDescription": settings["demandDescription"],
                "businessDirection": settings["businessDirectionLabel"],
                "strategy": self._strategy_context(settings, settings.get("planning") or {}),
                "notesCount": len(notes),
                "leadsCount": len(leads),
                "topLeads": leads[:12],
                "productDistribution": distribution,
                "qa": qa,
            },
            purpose="获客报告生成",
        )
        report["productDistribution"] = report.get("productDistribution") or distribution
        emit("success", "报告 Agent", "获客报告已生成。")
        return report

    def _reflect_and_evolve(
        self,
        runner: StructuredAgentRunner,
        evaluator: HarnessEvaluator,
        task_id: int | None,
        notes: list[dict[str, Any]],
        leads: list[dict[str, Any]],
        report: dict[str, Any],
        qa: dict[str, Any],
        emit: Any,
    ) -> dict[str, Any]:
        emit("info", "复盘进化 Agent", "复盘任务并生成可评估进化候选...")
        agent_runs = self.storage.list_agent_runs(task_id) if self.storage and task_id else []
        reflection = runner.run(
            "reflection_evolution_agent",
            {"notesCount": len(notes), "leadsCount": len(leads), "report": report, "qa": qa, "agentRuns": agent_runs},
            purpose="任务复盘与进化候选生成",
        )
        if self.storage and task_id:
            self.storage.save_task_summary(task_id, reflection)
            for case in reflection.get("eval_cases") or []:
                self.storage.save_eval_case(task_id, case)
            self._create_prompt_candidates(task_id, reflection, evaluator)
        emit("success", "复盘进化 Agent", str(reflection.get("evolution_summary") or "复盘完成。"), reflection)
        return reflection

    def _create_prompt_candidates(self, task_id: int, reflection: dict[str, Any], evaluator: HarnessEvaluator) -> None:
        for item in reflection.get("prompt_improvements") or []:
            agent_key = str(item.get("agent_key") or "").strip()
            prompt_patch = str(item.get("prompt_patch") or "").strip()
            if not agent_key or not prompt_patch or not self.storage:
                continue
            active = self.storage.get_active_prompt(agent_key)
            if not active:
                continue
            candidate_prompt = str(active.get("prompt") or "") + "\n\n# 进化候选\n" + prompt_patch
            candidate_id = self.storage.create_prompt_candidate(
                agent_key=agent_key,
                agent_name=str(active.get("agentName") or agent_key),
                base_version=str(active.get("version") or "v1"),
                prompt=candidate_prompt,
                source_task_id=task_id,
                notes=str(item.get("title") or "复盘候选改进"),
            )
            expected_gain = _expected_gain(item.get("expected_gain"))
            has_safety_risk = bool(item.get("risk")) and "高" in str(item.get("risk"))
            evaluator.consider_candidate(
                task_id=task_id,
                agent_key=agent_key,
                candidate_prompt_id=candidate_id,
                active_score=float(active.get("score") or 0.0),
                expected_gain=expected_gain,
                has_safety_risk=has_safety_risk,
            )

    def _audit_planned_keywords(self, task_id: int | None, keywords: list[str], planning: dict[str, Any]) -> None:
        hypotheses = planning.get("hypotheses") or []
        by_keyword = {str(item.get("keyword") or ""): item for item in hypotheses if isinstance(item, dict)}
        items = []
        for keyword in keywords:
            hypothesis = by_keyword.get(keyword) or {}
            items.append({
                "stage": "search",
                "entity_type": "keyword",
                "entity_id": keyword,
                "source_keyword": keyword,
                "decision": "planned",
                "reason": hypothesis.get("intent") or planning.get("strategy_summary") or "搜索规划 Agent 生成的关键词。",
                "evidence": hypothesis.get("expected_signals") or planning.get("evidence") or [],
                "confidence": planning.get("confidence"),
                "payload": {"planning": planning, "hypothesis": hypothesis},
            })
        self._save_audit(task_id, items)

    def _save_audit(self, task_id: int | None, items: list[dict[str, Any]]) -> None:
        if self.storage and task_id and items:
            self.storage.save_audit_decisions(task_id, items)

    def _strategy_context(self, settings: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
        return {
            "demandDescription": settings.get("demandDescription", ""),
            "userConstraints": settings.get("userConstraints", {}),
            "strategySummary": planning.get("strategy_summary") or planning.get("decision") or "",
            "targetCustomerProfile": planning.get("target_customer_profile") or "",
            "exclusions": planning.get("exclusions") or [],
            "hypotheses": planning.get("hypotheses") or [],
            "availableProducts": self._product_labels(),
        }

    def _lead_audit_item(self, comment: dict[str, Any], decision: dict[str, Any], final_decision: str, fallback_reason: str) -> dict[str, Any]:
        note = comment.get("note") or {}
        score = min(max(int(decision.get("score") or 0), 0), 100)
        return {
            "stage": "lead_signal",
            "entity_type": "comment",
            "entity_id": comment.get("comment_id") or decision.get("comment_id") or "",
            "note_id": comment.get("note_id") or decision.get("note_id") or "",
            "source_keyword": note.get("source_keyword", ""),
            "decision": final_decision,
            "reason": decision.get("reason") or fallback_reason,
            "evidence": decision.get("evidence") or comment.get("content", ""),
            "score": score,
            "confidence": decision.get("confidence"),
            "priority": decision.get("priority") or _priority_from_score(score),
            "risk": decision.get("risk") or "",
            "payload": {
                "comment": {
                    "comment_id": comment.get("comment_id"),
                    "note_id": comment.get("note_id"),
                    "content": comment.get("content", ""),
                    "ip": comment.get("ip", ""),
                    "note": note,
                },
                "decision": decision,
            },
        }

    def _text_value(self, value: Any, fallback: str = "") -> str:
        if isinstance(value, list):
            return "；".join(str(item) for item in value if item)
        if value in (None, ""):
            return fallback
        return str(value)

    def _lead_from_decision(self, comment: dict[str, Any], decision: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        note = comment.get("note") or {}
        user_id = str(comment.get("user_id") or "")
        nickname = str(comment.get("nickname") or "")
        lead_id = f"{comment.get('note_id')}:{comment.get('comment_id')}"
        score = min(max(int(decision.get("score") or 0), 0), 100)
        priority = str(decision.get("priority") or _priority_from_score(score)).upper()[:1]
        evidence = self._text_value(decision.get("evidence"), comment.get("content", ""))
        return {
            "lead_id": lead_id,
            "comment_id": comment.get("comment_id"),
            "user_id": redact_text(user_id, 4) if settings["redactUser"] else user_id,
            "nickname": redact_text(nickname, 1) if settings["redactUser"] else nickname,
            "raw_user_id": "" if settings["redactUser"] else user_id,
            "content": comment.get("content", ""),
            "evidence": evidence,
            "ip": comment.get("ip", ""),
            "score": score,
            "priority": priority if priority in {"A", "B", "C", "D"} else _priority_from_score(score),
            "stage": decision.get("stage") or "需求观察阶段",
            "confidence": float(decision.get("confidence") or 0.0),
            "matched_products": [],
            "recommended_products": "",
            "reason": decision.get("reason") or decision.get("decision") or "",
            "risk": decision.get("risk") or "",
            "demand_summary": decision.get("demand_summary") or "",
            "note_id": comment.get("note_id"),
            "note_title": note.get("title", ""),
            "note_url": note.get("url", ""),
            "feedback": "待人工判断",
            "outreach_copy": "",
            "source_comment": comment,
        }

    def _comment_brief(self, comment: dict[str, Any], note: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        user_info = comment.get("user_info") or {}
        content = str(comment.get("content") or "")
        comment_id = str(comment.get("id") or abs(hash(f"{note.get('id')}:{content}")))
        return {
            "comment_id": comment_id,
            "note_id": note.get("id"),
            "nickname": user_info.get("nickname") or user_info.get("nick_name") or "",
            "user_id": user_info.get("user_id") or "",
            "content": content,
            "ip": comment.get("ip_location", ""),
            "note": self._note_brief(note),
        }

    def _note_brief(self, note: dict[str, Any]) -> dict[str, Any]:
        return {
            "note_id": note.get("id"),
            "title": note.get("title"),
            "desc": str(note.get("desc") or "")[:800],
            "tags": note.get("tags") or [],
            "author": note.get("author"),
            "publish_date": note.get("publish_date"),
            "liked": note.get("liked", 0),
            "collected": note.get("collected", 0),
            "comments": note.get("comments", 0),
            "shared": note.get("shared", 0),
            "engagement": note.get("engagement", 0),
            "url": note.get("url"),
            "source_keyword": note.get("source_keyword", ""),
        }

    def _audit_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "agent": item.get("agentName") or item.get("agentKey"),
                "message": item.get("purpose") or item.get("decision") or item.get("error") or "Agent 调用",
                "data": item,
            }
            for item in trace[-100:]
        ]

    def _safety(self, settings: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "read-only",
            "actions": "status/search/read/comments only; no like/favorite/comment/follow/post/private message",
            "xhsCommand": " ".join(getattr(self.adapter, "command", [])),
            "delayMin": settings.get("delayMin"),
            "delayMax": settings.get("delayMax"),
        }

    def _product_labels(self) -> list[str]:
        return [item["label"] for item in PRODUCTS]


def _priority_from_score(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _expected_gain(value: Any) -> float:
    if isinstance(value, (int, float)):
        return min(max(float(value), 0.0), 0.15)
    text = str(value or "")
    if "高" in text:
        return 0.06
    if "中" in text:
        return 0.04
    if "低" in text:
        return 0.02
    return 0.03


def _normalize_products(products: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(products, list):
        products = [products] if products else []
    for index, product in enumerate(products):
        if isinstance(product, dict):
            tag = str(product.get("tag") or product.get("label") or f"product_{index}")
            label = str(product.get("label") or tag)
            normalized.append({"tag": tag, "label": label, "matched_terms": product.get("matched_terms", []), "pitch": product.get("pitch", "")})
        elif product:
            label = str(product)
            normalized.append({"tag": label, "label": label, "matched_terms": [], "pitch": ""})
    return normalized
