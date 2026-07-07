from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from typing import Any

from llm_client import call_llm
from product_catalog import (
    BUSINESS_DIRECTIONS,
    NEGATIVE_TERMS,
    build_keywords,
    business_direction_options,
    is_negative_text,
    match_products,
    product_distribution,
)
from prompt_templates import PROMPT_VERSION, SALES_REPORT_SYSTEM_PROMPT, sales_report_user_prompt
from sales_scoring import make_demand_summary, make_outreach_copy, redact_text, score_signal


METRIC_LABELS = {
    "liked": "点赞量",
    "collected": "收藏量",
    "comments": "评论量",
    "shared": "转发量",
    "engagement": "综合互动",
}


def to_int(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    if text.endswith("万"):
        try:
            return int(float(text[:-1]) * 10000)
        except ValueError:
            return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def extract_note(item: dict[str, Any], rank: int = 0) -> dict[str, Any] | None:
    if item.get("model_type") != "note":
        return None
    card = item.get("note_card") or {}
    note_id = item.get("id") or card.get("note_id") or card.get("id")
    if not note_id:
        return None
    inter = card.get("interact_info") or {}
    user = card.get("user") or {}
    title = card.get("display_title") or card.get("title") or "未命名笔记"
    desc = card.get("desc") or ""
    tags = [t.get("name") for t in card.get("tag_list", []) if t.get("name")]
    liked = to_int(inter.get("liked_count"))
    collected = to_int(inter.get("collected_count"))
    comments = to_int(inter.get("comment_count"))
    shared = to_int(inter.get("share_count", inter.get("shared_count")))
    publish_time_ms = card.get("time") or card.get("last_update_time") or 0
    publish_date = ""
    if publish_time_ms:
        try:
            publish_date = time.strftime("%Y-%m-%d", time.localtime(publish_time_ms / 1000.0))
        except Exception:
            publish_date = ""
    if not publish_date:
        for tag in card.get("corner_tag_info", []):
            if tag.get("type") == "publish_time" and tag.get("text"):
                publish_date = tag.get("text")
                break

    return {
        "rank": rank,
        "id": note_id,
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "title": title,
        "author": user.get("nickname") or user.get("nick_name") or "",
        "author_user_id": user.get("id") or user.get("user_id") or "",
        "liked": liked,
        "collected": collected,
        "comments": comments,
        "shared": shared,
        "engagement": liked + collected + comments + shared,
        "images": len(card.get("image_list") or []),
        "tags": tags,
        "desc": desc,
        "publish_date": publish_date,
        "publish_time_ms": publish_time_ms,
    }


def merge_note(base: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return base
    merged = dict(base)
    for key in ["title", "author", "author_user_id", "desc", "publish_date", "publish_time_ms"]:
        if detail.get(key):
            merged[key] = detail[key]
    for key in ["liked", "collected", "comments", "shared", "images"]:
        if detail.get(key):
            merged[key] = detail[key]
    if detail.get("tags"):
        merged["tags"] = detail["tags"]
    merged["engagement"] = merged["liked"] + merged["collected"] + merged["comments"] + merged["shared"]
    return merged


def note_text(note: dict[str, Any]) -> str:
    return " ".join([note.get("title", ""), note.get("desc", ""), " ".join(note.get("tags") or [])])


def within_time_filter(note: dict[str, Any], time_filter: str, now: float) -> bool:
    if time_filter == "all":
        return True
    publish_time_ms = note.get("publish_time_ms") or 0
    if not publish_time_ms and note.get("publish_date"):
        try:
            publish_time_ms = time.mktime(time.strptime(note["publish_date"], "%Y-%m-%d")) * 1000.0
        except Exception:
            publish_time_ms = 0
    if not publish_time_ms:
        return True
    age_sec = now - (publish_time_ms / 1000.0)
    limits = {"month": 30 * 86400, "quarter": 90 * 86400, "year": 365 * 86400}
    return age_sec <= limits.get(time_filter, 10**12)


def is_relevant_note(keyword: str, note: dict[str, Any], direction: str) -> bool:
    text = note_text(note)
    if is_negative_text(text):
        return False
    if match_products(text):
        return True
    keyword = keyword.lower()
    fragments = [keyword]
    if len(keyword) >= 4:
        fragments.extend([keyword[:2], keyword[-2:]])
    elif len(keyword) == 3:
        fragments.extend([keyword[:2], keyword[1:]])
    if any(len(fragment) >= 2 and fragment in text.lower() for fragment in fragments):
        return True
    direction_keywords = BUSINESS_DIRECTIONS.get(direction, BUSINESS_DIRECTIONS["cloud_ai"])["keywords"]
    return any(term in text for term in direction_keywords)


def clean_words(text: str) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z]{2,}", text)
    stop_words = {"我们", "可以", "这个", "觉得", "没有", "大家", "非常", "真的", "一个", "怎么", "已经", "分享", "推荐"}
    return [word for word in words if word.lower() not in stop_words and word not in NEGATIVE_TERMS]


def top_terms(notes: list[dict[str, Any]], leads: list[dict[str, Any]]) -> dict[str, Any]:
    title_words: list[str] = []
    evidence_words: list[str] = []
    for note in notes:
        title_words.extend(clean_words(note.get("title", "")))
    for lead in leads:
        evidence_words.extend(clean_words(lead.get("evidence", "")))
    return {
        "title_words": Counter(title_words).most_common(10),
        "evidence_words": Counter(evidence_words).most_common(10),
    }


def deterministic_report(keyword: str, notes: list[dict[str, Any]], leads: list[dict[str, Any]]) -> dict[str, Any]:
    distribution = product_distribution(leads)
    terms = top_terms(notes, leads)
    top_products = "、".join(item["label"] for item in distribution[:5]) or "暂无高置信产品匹配"
    high_leads = [lead for lead in leads if lead.get("priority") == "A"]
    return {
        "summary": f"本次围绕「{keyword}」共分析 {len(notes)} 篇笔记，识别 {len(leads)} 条可跟进线索，其中 A 级 {len(high_leads)} 条。主要机会集中在：{top_products}。",
        "insights": [
            f"产品分布：{top_products}。",
            "高频标题词：" + "、".join(f"{word}({count})" for word, count in terms["title_words"][:6]) if terms["title_words"] else "暂无足够标题高频词。",
            "高频需求词：" + "、".join(f"{word}({count})" for word, count in terms["evidence_words"][:6]) if terms["evidence_words"] else "暂无足够评论需求词。",
            "所有线索均保留原文证据，跟进前需人工核验语境。",
        ],
        "recommended_actions": [
            "优先跟进 A 级线索，先用低打扰话术确认需求是否仍存在。",
            "B 级线索进入线索池二次筛选，结合账号主页和公开信息人工判断。",
            "C/D 级线索只作为市场洞察或负样本，不建议直接触达。",
        ],
        "risks": [
            "小红书公开评论不等同于明确采购授权，禁止自动私信或评论。",
            "遇到验证码、风控或登录过期时应停止采集并人工处理。",
        ],
        "productDistribution": distribution,
    }


class LeadPipeline:
    def __init__(self, adapter: Any, config: dict[str, Any], storage: Any | None = None) -> None:
        self.adapter = adapter
        self.config = config
        self.storage = storage

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
        emit("info", "登录检查", "检查小红书登录态...")
        self.adapter.ensure_authenticated()
        emit("success", "登录检查", "小红书登录态有效。")
        xhs_config = self.config.get("xhs") or {}
        sales_config = self.config.get("sales") or {}
        business_direction = str(payload.get("businessDirection") or sales_config.get("default_business_direction") or "cloud_ai")
        industry = str(payload.get("industry") or "").strip()
        region = str(payload.get("region") or "").strip()
        keyword_input = str(payload.get("keyword") or "").strip()
        metric = str(payload.get("metric") or "engagement")
        if metric not in METRIC_LABELS:
            metric = "engagement"
        count = min(max(int(payload.get("count", 10)), 1), int(xhs_config.get("max_count", 20)))
        min_score = min(max(int(payload.get("minScore", sales_config.get("default_min_score", 60))), 0), 100)
        time_filter = str(payload.get("timeFilter") or "all")
        read_detail = bool(payload.get("readDetail", True))
        scan_leads = bool(payload.get("scanLeads", True))
        delay_min = float(payload.get("delayMin", xhs_config.get("read_delay_min", 7.0)))
        delay_max = float(payload.get("delayMax", xhs_config.get("read_delay_max", 60.0)))
        delay_min = min(max(delay_min, 0.0), 120.0)
        delay_max = min(max(delay_max, delay_min), 120.0)
        redact_user = bool(sales_config.get("redact_user", False))

        keywords = build_keywords(business_direction, industry=industry, region=region, custom=keyword_input)
        audit_trail = [{"agent": "关键词规划 Agent", "message": f"生成 {len(keywords)} 个搜索词", "data": keywords}]
        emit("info", "关键词规划 Agent", f"生成 {len(keywords)} 个搜索词。", keywords)
        progress({"stage": "search", "percent": 10, "current": 0, "total": len(keywords)})
        notes = self.collect_notes(keywords, business_direction, count, metric, time_filter, page, audit_trail, stop_checker, emit, progress)
        selected = notes[: count * max(1, len(keywords))]

        if read_detail:
            progress({"stage": "read_detail", "percent": 35, "current": 0, "total": len(selected)})
            selected = self.enrich_notes(selected, delay_min, delay_max, audit_trail, stop_checker, emit, progress)
        else:
            emit("info", "搜索读取 Agent", "已跳过详情读取。")

        leads: list[dict[str, Any]] = []
        total_comments = 0
        if scan_leads:
            progress({"stage": "comments", "percent": 60, "current": 0, "total": len(selected)})
            leads, total_comments = self.collect_leads(selected, business_direction, min_score, redact_user, delay_min, delay_max, task_id, audit_trail, stop_checker, emit, progress)
        else:
            emit("info", "线索信号 Agent", "已跳过评论商机识别。")

        selected.sort(key=lambda note: note.get(metric, 0), reverse=True)
        for idx, note in enumerate(selected, 1):
            note["rank"] = idx
            if self.storage and task_id:
                self.storage.save_note(task_id, note)

        llm_config = self.config.get("llm") or {}
        if not llm_config.get("api_key"):
            emit("warning", "报告 Agent", "未配置 LLM API Key，使用规则摘要生成报告。")
        else:
            emit("info", "报告 Agent", "生成摘要报告...")
        progress({"stage": "report", "percent": 90, "current": 1, "total": 1})
        report = self.make_report(keyword_input or "、".join(keywords[:3]), selected, leads, task_id)
        if report.get("llm_error"):
            emit("warning", "报告 Agent", "LLM 报告生成失败，已回退到规则摘要。", {"error": report.get("llm_error")})
        emit("success", "质检汇总 Agent", f"任务结果已生成：{len(selected)} 篇笔记，{len(leads)} 条达标线索。")
        progress({"stage": "done", "percent": 100, "current": 1, "total": 1})
        return {
            "keyword": keyword_input or "、".join(keywords),
            "businessDirection": business_direction,
            "businessDirectionLabel": BUSINESS_DIRECTIONS.get(business_direction, BUSINESS_DIRECTIONS["cloud_ai"])["label"],
            "industry": industry,
            "region": region,
            "metric": metric,
            "metricLabel": METRIC_LABELS[metric],
            "count": len(selected),
            "notes": selected,
            "leads": {
                "keyword": keyword_input,
                "scannedNotesCount": len(selected),
                "totalCommentsScanned": total_comments,
                "leadsCount": len(leads),
                "minScore": min_score,
                "leads": leads,
                "productDistribution": product_distribution(leads),
            },
            "report": report,
            "auditTrail": audit_trail,
            "safety": {
                "mode": "read-only",
                "actions": "status/search/read/comments only; no like/favorite/comment/follow/post/private message",
                "xhsCommand": " ".join(self.adapter.command),
                "delayMin": delay_min,
                "delayMax": delay_max,
            },
        }

    def collect_notes(self, keywords: list[str], direction: str, count: int, metric: str, time_filter: str, page: int, audit_trail: list[dict[str, Any]], stop_checker: Any | None, emit: Any, progress: Any) -> list[dict[str, Any]]:
        seen: set[str] = set()
        notes: list[dict[str, Any]] = []
        now = time.time()
        for index, keyword in enumerate(keywords, 1):
            if stop_checker and stop_checker():
                break
            emit("info", "搜索读取 Agent", f"搜索「{keyword}」第 {page} 页...")
            raw = self.adapter.search(keyword, page=page)
            items = (raw.get("data") or {}).get("items") or []
            kept = 0
            for item in items:
                note = extract_note(item)
                if not note or note["id"] in seen:
                    continue
                if not within_time_filter(note, time_filter, now):
                    continue
                if not is_relevant_note(keyword, note, direction):
                    continue
                seen.add(note["id"])
                notes.append(note)
                kept += 1
            audit_trail.append({"agent": "搜索读取 Agent", "message": f"搜索「{keyword}」返回 {len(items)} 条，保留 {kept} 条相关笔记"})
            emit("success", "搜索读取 Agent", f"搜索「{keyword}」返回 {len(items)} 条，保留 {kept} 条相关笔记。")
            progress({"stage": "search", "percent": 10 + int(20 * index / max(1, len(keywords))), "current": index, "total": len(keywords)})
        notes.sort(key=lambda note: note.get(metric, 0), reverse=True)
        audit_trail.append({"agent": "相关性判断 Agent", "message": f"去重过滤后得到 {len(notes)} 篇云/AI相关笔记"})
        emit("success", "相关性判断 Agent", f"去重过滤后得到 {len(notes)} 篇云/AI相关笔记。")
        return notes

    def enrich_notes(self, notes: list[dict[str, Any]], delay_min: float, delay_max: float, audit_trail: list[dict[str, Any]], stop_checker: Any | None, emit: Any, progress: Any) -> list[dict[str, Any]]:
        enriched = []
        for idx, note in enumerate(notes):
            if stop_checker and stop_checker():
                break
            if idx > 0 and delay_max > 0:
                emit("info", "延迟保护", f"合规延迟后读取下一篇详情：{note.get('title', '')[:24]}")
                time.sleep(random.uniform(delay_min, delay_max))
            try:
                emit("info", "搜索读取 Agent", f"读取详情 {idx + 1}/{len(notes)}：{note.get('title', '')[:24]}")
                detail_raw = self.adapter.read_note(note["id"])
                detail_items = (detail_raw.get("data") or {}).get("items") or []
                detail = extract_note(detail_items[0]) if detail_items else None
                enriched.append(merge_note(note, detail))
            except Exception as exc:
                note["read_error"] = str(exc)[:300]
                emit("warning", "搜索读取 Agent", f"读取详情失败：{note.get('title', '')[:24]}", {"error": str(exc)[:300]})
                enriched.append(note)
            progress({"stage": "read_detail", "percent": 35 + int(20 * (idx + 1) / max(1, len(notes))), "current": idx + 1, "total": len(notes)})
        audit_trail.append({"agent": "搜索读取 Agent", "message": f"读取详情完成：{len(enriched)} 篇"})
        emit("success", "搜索读取 Agent", f"读取详情完成：{len(enriched)} 篇。")
        return enriched

    def collect_leads(self, notes: list[dict[str, Any]], direction: str, min_score: int, redact_user: bool, delay_min: float, delay_max: float, task_id: int | None, audit_trail: list[dict[str, Any]], stop_checker: Any | None, emit: Any, progress: Any) -> tuple[list[dict[str, Any]], int]:
        leads: list[dict[str, Any]] = []
        total_comments = 0
        for idx, note in enumerate(notes):
            if stop_checker and stop_checker():
                break
            if idx > 0 and delay_max > 0:
                emit("info", "延迟保护", f"合规延迟后读取下一篇评论：{note.get('title', '')[:24]}")
                time.sleep(random.uniform(delay_min, delay_max))
            try:
                emit("info", "线索信号 Agent", f"扫描评论 {idx + 1}/{len(notes)}：{note.get('title', '')[:24]}")
                cmt_raw = self.adapter.comments(note["id"])
                comments = (cmt_raw.get("data") or {}).get("comments") or []
                total_comments += len(comments)
                note_leads = 0
                for comment in comments:
                    if self.storage and task_id:
                        self.storage.save_comment(task_id, note["id"], comment)
                    lead = self.comment_to_lead(comment, note, direction, redact_user)
                    if int(lead["score"]) >= min_score:
                        leads.append(lead)
                        note_leads += 1
                        if self.storage and task_id:
                            self.storage.save_lead(task_id, lead)
                emit("success", "线索信号 Agent", f"扫描 {len(comments)} 条评论，识别 {note_leads} 条达标线索。")
            except Exception as exc:
                note["comment_error"] = str(exc)[:300]
                emit("warning", "线索信号 Agent", f"评论扫描失败：{note.get('title', '')[:24]}", {"error": str(exc)[:300]})
            progress({"stage": "comments", "percent": 60 + int(25 * (idx + 1) / max(1, len(notes))), "current": idx + 1, "total": len(notes)})
        leads.sort(key=lambda lead: int(lead.get("score", 0)), reverse=True)
        audit_trail.append({"agent": "线索信号 Agent", "message": f"扫描 {total_comments} 条评论，识别 {len(leads)} 条达到阈值的商机信号"})
        audit_trail.append({"agent": "产品匹配 Agent", "message": f"完成产品匹配：{len(product_distribution(leads))} 个产品方向"})
        emit("success", "产品匹配 Agent", f"完成产品匹配：{len(product_distribution(leads))} 个产品方向。")
        return leads, total_comments

    def comment_to_lead(self, comment: dict[str, Any], note: dict[str, Any], direction: str, redact_user: bool) -> dict[str, Any]:
        user_info = comment.get("user_info") or {}
        content = comment.get("content", "")
        scoring = score_signal(content, note)
        nickname = user_info.get("nickname") or user_info.get("nick_name") or ""
        user_id = user_info.get("user_id") or ""
        lead_id = f"{note['id']}:{comment.get('id') or abs(hash(content))}"
        demand_summary = make_demand_summary(content, scoring["matched_products"])
        lead = {
            "lead_id": lead_id,
            "comment_id": comment.get("id"),
            "user_id": redact_text(user_id, 4) if redact_user else user_id,
            "nickname": redact_text(nickname, 1) if redact_user else nickname,
            "raw_user_id": user_id if not redact_user else "",
            "content": content,
            "evidence": content,
            "ip": comment.get("ip_location", ""),
            "score": scoring["score"],
            "priority": scoring["priority"],
            "stage": scoring["stage"],
            "matched_products": scoring["matched_products"],
            "recommended_products": "、".join(p["label"] for p in scoring["matched_products"][:3]),
            "reason": scoring["reason"],
            "risk": scoring["risk"],
            "demand_summary": demand_summary,
            "note_id": note["id"],
            "note_title": note["title"],
            "note_url": note["url"],
            "feedback": "待人工判断",
        }
        lead["outreach_copy"] = make_outreach_copy(lead)
        return lead

    def make_report(self, keyword: str, notes: list[dict[str, Any]], leads: list[dict[str, Any]], task_id: int | None = None) -> dict[str, Any]:
        fallback = deterministic_report(keyword, notes, leads)
        llm_config = self.config.get("llm") or {}
        api_key = llm_config.get("api_key") or ""
        if not api_key:
            return fallback
        payload = {
            "keyword": keyword,
            "notes_count": len(notes),
            "leads_count": len(leads),
            "top_leads": [
                {
                    "score": lead["score"],
                    "priority": lead["priority"],
                    "demand_summary": lead["demand_summary"],
                    "products": lead["recommended_products"],
                    "evidence": lead["evidence"][:160],
                }
                for lead in leads[:12]
            ],
            "product_distribution": product_distribution(leads),
        }
        try:
            response = call_llm(
                [
                    {"role": "system", "content": SALES_REPORT_SYSTEM_PROMPT},
                    {"role": "user", "content": sales_report_user_prompt(payload)},
                ],
                api_key=api_key,
                api_base=llm_config.get("api_base"),
                model=llm_config.get("model"),
            )
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
                cleaned = re.sub(r"\n```$", "", cleaned).strip()
            data = json.loads(cleaned)
            report = {
                "summary": data.get("summary") or fallback["summary"],
                "insights": data.get("insights") or fallback["insights"],
                "recommended_actions": data.get("recommended_actions") or fallback["recommended_actions"],
                "risks": data.get("risks") or fallback["risks"],
                "productDistribution": fallback["productDistribution"],
                "promptVersion": PROMPT_VERSION,
            }
            if self.storage and task_id:
                self.storage.save_prompt_run(task_id, PROMPT_VERSION, llm_config.get("model", ""), str(payload)[:1000], json.dumps(report, ensure_ascii=False)[:1000])
            return report
        except Exception as exc:
            if self.storage and task_id:
                self.storage.save_prompt_run(task_id, PROMPT_VERSION, llm_config.get("model", ""), str(payload)[:1000], error=str(exc)[:1000])
            fallback["llm_error"] = str(exc)[:300]
            return fallback


def public_config() -> dict[str, Any]:
    return {
        "businessDirections": business_direction_options(),
        "metricLabels": METRIC_LABELS,
    }
