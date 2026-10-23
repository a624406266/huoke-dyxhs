"""Versioned local plans, independent human decisions, and reproducible evidence."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from json_utils import canonical_json


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class WorkbenchStore:
    def __init__(self, storage: Any):
        self.storage = storage
        with storage.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS search_plans (
                  id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS human_reviews (
                  id INTEGER PRIMARY KEY, lead_id TEXT NOT NULL, basis TEXT NOT NULL,
                  task_id INTEGER NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
                  actor INTEGER NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS review_basis ON human_reviews(lead_id,basis,id);
                CREATE TABLE IF NOT EXISTS task_templates (
                  name TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS benchmark_datasets (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, prompt_id INTEGER,
                  prompt_hash TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            """)

    def save_plan(self, plan: dict) -> dict:
        plan_id = uuid.uuid4().hex
        with self.storage.connect() as db:
            db.execute("INSERT INTO search_plans VALUES(?,?,?)", (plan_id, canonical_json(plan), time.time()))
        return {"planId": plan_id, "plan": plan}

    def get_plan(self, plan_id: str) -> dict:
        with self.storage.connect() as db:
            row = db.execute("SELECT payload FROM search_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("搜索计划不存在，请先预览计划。")
        return json.loads(row[0])

    def save_template(self, name: str, plan: dict) -> dict:
        name = str(name).strip()
        if not name or len(name) > 100:
            raise ValueError("模板名称需要 1–100 个字符。")
        with self.storage.connect() as db:
            db.execute("INSERT INTO task_templates VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload,updated=excluded.updated", (name, canonical_json(plan), time.time()))
        return {"name": name, "plan": plan}

    def templates(self) -> list:
        with self.storage.connect() as db:
            return [{"name": r[0], "plan": json.loads(r[1])} for r in db.execute("SELECT name,payload FROM task_templates ORDER BY updated DESC")]

    @staticmethod
    def review_basis(lead: dict, plan: dict) -> str:
        # A rerun of identical evidence and intent reuses the human decision. Changed
        # evidence or intent must not inherit an approval from a different task.
        intent = {k: plan.get(k) for k in ("seedKeywords", "keywords", "searchGoal", "excludeKeywords")}
        # A specialized service changes the scope of an otherwise identical
        # general search, so its approvals must be independent.
        if plan.get("offeringId") and plan["offeringId"] != "general":
            intent["offeringId"] = plan["offeringId"]
        return digest({"source": {k: lead.get(k) for k in ("platform", "note_id", "comment_id", "comment", "content", "evidence", "note_url")},
                       "intent": intent})

    def annotate(self, result: dict, plan: dict) -> dict:
        leads = (result.get("leads") or {}).get("leads") or []
        if not leads:
            return result
        requested = set()
        for lead in leads:
            basis = self.review_basis(lead, plan)
            lead["reviewBasis"] = basis
            requested.add((str(lead.get("lead_id") or ""), basis))
        keys = list(requested)
        reviews = {}
        # One connection and bounded batches also work with SQLite builds whose
        # bind-variable limit is 999. The review_basis index finds the newest row.
        with self.storage.connect() as db:
            for offset in range(0, len(keys), 400):
                batch = keys[offset:offset + 400]
                placeholders = ",".join("(?,?)" for _ in batch)
                rows = db.execute(f"""WITH requested(lead_id,basis) AS (VALUES {placeholders})
                    SELECT r.lead_id,r.basis,r.status,r.note,r.actor,r.created,r.id
                    FROM requested q JOIN human_reviews r ON r.id=(
                        SELECT id FROM human_reviews WHERE lead_id=q.lead_id AND basis=q.basis
                        ORDER BY id DESC LIMIT 1)""", [value for key in batch for value in key]).fetchall()
                for row in rows:
                    reviews[(row["lead_id"], row["basis"])] = {
                        "status": row["status"], "note": row["note"], "actor": row["actor"],
                        "updatedAt": row["created"], "id": row["id"]}
        for lead in leads:
            review = reviews.get((str(lead.get("lead_id") or ""), lead["reviewBasis"]))
            lead["review"] = dict(review) if review else {"status": "pending", "note": ""}
        return result

    def review(self, payload: dict, actor: int) -> dict:
        task_id, lead_id = int(payload.get("taskId") or 0), str(payload.get("leadId") or "")
        task = self.storage.get_task(task_id, sales_user_id=0)
        if not task:
            raise ValueError("任务不存在。")
        lead = next((x for x in ((task.get("result") or {}).get("leads") or {}).get("leads", []) if x.get("lead_id") == lead_id), None)
        if not lead:
            raise ValueError("线索不属于该任务。")
        status = payload.get("status")
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError("审核状态不合法。")
        basis = self.review_basis(lead, (task.get("payload") or {}).get("confirmedPlan") or {})
        if payload.get("reviewBasis") and payload["reviewBasis"] != basis:
            raise ValueError("原文或搜索目标已变化，请刷新后重新审核。")
        note = str(payload.get("note") or "")
        with self.storage.connect() as db:
            cursor = db.execute("INSERT INTO human_reviews(lead_id,basis,task_id,status,note,actor,created) VALUES(?,?,?,?,?,?,?)", (lead_id, basis, task_id, status, note, actor, time.time()))
            review_id = cursor.lastrowid
        return {"leadId": lead_id, "review": {"id": review_id, "status": status, "note": note}, "reviewBasis": basis}

    def review_history(self, lead_id: str, task_id: int | None = None) -> list:
        if task_id is not None and (type(task_id) is not int or task_id <= 0):
            raise ValueError("任务编号必须是正整数。")
        query = "SELECT * FROM human_reviews WHERE lead_id=?"
        params: tuple = (lead_id,)
        if task_id is not None:
            query += " AND task_id=?"
            params += (task_id,)
        with self.storage.connect() as db:
            rows = db.execute(query + " ORDER BY id DESC", params).fetchall()
        return [dict(r) for r in rows]

    def import_dataset(self, payload: dict) -> dict:
        samples = payload.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("请提供带真实来源和人工标签的 samples 数组。")
        ids, groups = set(), {}
        for sample in samples:
            specialized = isinstance(sample, dict) and sample.get("agentKey") in {"critic_qa_agent", "report_agent"}
            required = ("id", "noteId", "topic", "sourceUrl", "collectedAt", "text", "split", "annotator") + (() if specialized else ("labels",))
            if not isinstance(sample, dict) or any(not sample.get(k) for k in required):
                raise ValueError("样本缺少 id/noteId/topic/sourceUrl/collectedAt/text/split/labels/annotator。")
            if sample["id"] in ids or sample["split"] not in {"development", "acceptance"}:
                raise ValueError("样本 ID 重复或 split 不合法。")
            ids.add(sample["id"])
            if sample["noteId"] in groups and groups[sample["noteId"]] != sample["split"]:
                raise ValueError("同一笔记不得跨调试集和冻结验收集。")
            groups[sample["noteId"]] = sample["split"]
            from agent_harness.offering import GENERAL, normalize_offering
            normalize_offering(sample.get("offeringId", GENERAL))
            labels = sample.get("labels") or {}
            if specialized:
                context, expected = sample.get("input"), sample.get("expected")
                if not isinstance(context, dict) or not isinstance(expected, dict):
                    raise ValueError("QA/报告专用样本必须有冻结 input 和人工 expected 对象。")
                if not isinstance(context.get("plan"), dict):
                    raise ValueError("专用样本 input.plan 必须包含任务的确认目标。")
                if normalize_offering(context["plan"].get("offeringId", GENERAL)) != normalize_offering(sample.get("offeringId", GENERAL)):
                    raise ValueError("样本服务范围与冻结输入 plan.offeringId 不一致。")
                if sample["agentKey"] == "critic_qa_agent":
                    targets = expected.get("issueTargets")
                    if type(expected.get("approved")) is not bool or not isinstance(targets, list) or any(not isinstance(x, str) or not x for x in targets):
                        raise ValueError("QA 人工期望必须包含 approved 布尔值和 issueTargets 字符串数组。")
                    if not isinstance(context.get("leads"), list) or not context["leads"]:
                        raise ValueError("QA 冻结输入必须有待质检 leads。")
                else:
                    facts, forbidden = expected.get("requiredFacts"), expected.get("forbiddenClaims")
                    if not isinstance(facts, list) or not facts or not isinstance(forbidden, list) or any(not isinstance(x, str) or not x.strip() for x in [*facts, *forbidden]):
                        raise ValueError("报告期望必须有非空 requiredFacts 和 forbiddenClaims 字符串数组。")
            elif not isinstance(labels, dict) or type(labels.get("relevant")) is not bool or labels.get("classification") not in {"demand", "uncertain", "information", "promotion", "irrelevant"}:
                raise ValueError("人工标签必须包含 relevant 布尔值及五类 classification。")
            if not str(sample["sourceUrl"]).startswith("https://"):
                raise ValueError("样本必须有可追溯 HTTPS 来源。")
        dataset_id = digest(samples)
        with self.storage.connect() as db:
            db.execute("INSERT OR IGNORE INTO benchmark_datasets VALUES(?,?,?,?)", (dataset_id, str(payload.get("name") or "人工标注集"), canonical_json(samples), time.time()))
        return {"datasetId": dataset_id, "count": len(samples), "frozen": True}

    def datasets(self) -> list:
        with self.storage.connect() as db:
            rows = db.execute("SELECT id,name,payload,created FROM benchmark_datasets ORDER BY created DESC").fetchall()
        return [{"datasetId": r[0], "name": r[1], "count": len(json.loads(r[2])), "createdAt": r[3]} for r in rows]

    def dataset(self, dataset_id: str) -> list:
        with self.storage.connect() as db:
            row = db.execute("SELECT payload FROM benchmark_datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise ValueError("冻结评测集不存在，请先导入人工标注样本。")
        return json.loads(row[0])

    def save_benchmark(self, result: dict) -> dict:
        run_id = uuid.uuid4().hex
        result = {**result, "runId": run_id, "createdAt": time.time()}
        with self.storage.connect() as db:
            db.execute("INSERT INTO benchmark_runs VALUES(?,?,?,?,?,?)", (run_id, result["datasetId"], result.get("promptId"), result["promptHash"], canonical_json(result), result["createdAt"]))
        return result

    def benchmarks(self, limit: int = 50) -> list:
        with self.storage.connect() as db:
            rows = db.execute("SELECT payload FROM benchmark_runs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def review_benchmark(self, payload: dict, actor: int) -> dict:
        run_id = str(payload.get("runId") or "")
        with self.storage.connect() as db:
            row = db.execute("SELECT payload FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError("评测记录不存在。")
            result = json.loads(row[0])
            expected = {str(r["id"]) for r in result.get("rows", [])}
            reviewed = {str(x) for x in payload.get("reviewedSampleIds", [])}
            if not expected or reviewed != expected:
                raise ValueError("请逐条核验此评测全部输出，并提供对应样本 ID。")
            if payload.get("noFabrication") is not True or payload.get("citationsVerified") is not True or not str(payload.get("note") or "").strip():
                raise ValueError("需明确核验引用与虚构信息，并填写人工复核说明。")
            result["humanReview"] = {"actor": actor, "createdAt": time.time(), "sampleIds": sorted(reviewed),
                                     "note": str(payload["note"]), "noFabrication": True, "citationsVerified": True}
            result["humanReviewComplete"] = True
            if result.get("agentKey") in {"critic_qa_agent", "report_agent"}:
                checks = payload.get("qualityReviews")
                if not isinstance(checks, list) or len(checks) != len(expected) or any(not isinstance(x, dict) for x in checks):
                    raise ValueError("专用评测需逐条提供 qualityReviews（sampleId、passed、note），不能用关键词匹配代替语义质量核验。")
                if {str(x.get("sampleId")) for x in checks} != expected or any(type(x.get("passed")) is not bool or not str(x.get("note") or "").strip() for x in checks):
                    raise ValueError("专用质量核验必须覆盖全部样本，逐条填写通过/不通过和理由。")
                result["qualityReviews"] = checks
                result["qualityReviewComplete"] = True
                result["passed"] = result.get("autoPassed") is True and all(x["passed"] for x in checks)
            else:
                result["passed"] = result.get("autoPassed") is True
            db.execute("UPDATE benchmark_runs SET payload=? WHERE id=?", (canonical_json(result), run_id))
        return result

    def promotion_proof(self, prompt_id: int, prompt: str, run_id: str) -> dict:
        with self.storage.connect() as db:
            row = db.execute("SELECT payload FROM benchmark_runs WHERE id=? AND prompt_id=? AND prompt_hash=?", (run_id, prompt_id, digest(prompt))).fetchone()
        result = json.loads(row[0]) if row else {}
        from agent_harness.offering import SERVICE_POLICY_VERSION
        if result.get("servicePolicyVersion") and result["servicePolicyVersion"] != SERVICE_POLICY_VERSION:
            raise ValueError("服务判断规则已变化，请使用当前规则重新评测后启用。")
        if not result.get("passed") or not result.get("humanReviewComplete") or result.get("split") != "acceptance":
            raise ValueError("该 Prompt 尚未通过真实执行的冻结验收集，请先评测再启用。")
        return result
