from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from storage import SalesStorage


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "sales_leads.db"

AGENT_STAGE = {
    "chief_orchestrator": "orchestrate",
    "planning_agent": "plan",
    "product_strategy_agent": "product_strategy",
    "critic_qa_agent": "qa",
    "report_agent": "report",
    "reflection_evolution_agent": "reflection",
}


def json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def text_value(value: Any, fallback: str = "") -> str:
    if value in (None, ""):
        return fallback
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def output_reason(agent_key: str, output: dict[str, Any]) -> str:
    if agent_key == "planning_agent":
        return text_value(output.get("strategy_summary") or output.get("decision"), "搜索规划 Agent 已生成搜索策略。")
    if agent_key == "report_agent":
        return text_value(output.get("summary") or output.get("decision"), "获客报告已生成。")
    if agent_key == "reflection_evolution_agent":
        return text_value(output.get("evolution_summary") or output.get("decision"), "复盘进化 Agent 已完成。")
    if agent_key == "critic_qa_agent":
        issues = output.get("issues") or []
        return text_value(output.get("decision"), f"QA 完成，发现 {len(issues)} 个问题。")
    return text_value(output.get("decision") or output.get("strategy_summary"), "Agent 阶段已完成。")


def output_decision(agent_key: str, output: dict[str, Any]) -> str:
    if agent_key == "critic_qa_agent":
        return "approved" if output.get("approved", True) else "needs_review"
    if agent_key == "report_agent":
        return "generated"
    return "completed"


def output_evidence(agent_key: str, output: dict[str, Any]) -> Any:
    if agent_key == "planning_agent":
        return output.get("keywords") or output.get("evidence") or []
    if agent_key == "critic_qa_agent":
        return output.get("issues") or output.get("evidence") or []
    if agent_key == "report_agent":
        return output.get("insights") or output.get("recommended_actions") or output.get("evidence") or []
    if agent_key == "reflection_evolution_agent":
        return output.get("prompt_improvements") or output.get("evidence") or []
    return output.get("evidence") or []


def exists(conn: sqlite3.Connection, task_id: int, item: dict[str, Any]) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM audit_decisions
        WHERE task_id = ? AND stage = ? AND entity_type = ? AND entity_id = ? AND decision = ?
        LIMIT 1
        """,
        (
            task_id,
            str(item.get("stage") or ""),
            str(item.get("entity_type") or ""),
            str(item.get("entity_id") or ""),
            str(item.get("decision") or ""),
        ),
    ).fetchone()
    return bool(row)


def stage_item(task_id: int, run: sqlite3.Row, output: dict[str, Any]) -> dict[str, Any]:
    agent_key = str(run["agent_key"] or "")
    stage = AGENT_STAGE[agent_key]
    return {
        "stage": stage,
        "entity_type": "agent",
        "entity_id": agent_key,
        "decision": output_decision(agent_key, output),
        "reason": output_reason(agent_key, output),
        "evidence": output_evidence(agent_key, output),
        "confidence": output.get("confidence", run["confidence"]),
        "risk": "；".join(str(item) for item in (output.get("risks") or [])[:5]),
        "payload": {
            "backfilled": True,
            "agent_key": agent_key,
            "agent_name": run["agent_name"],
            "agent_run_id": int(run["id"]),
            "prompt_version": run["prompt_version"],
            "purpose": run["purpose"],
            "output": output,
        },
    }


def extra_items(run: sqlite3.Row, output: dict[str, Any]) -> list[dict[str, Any]]:
    agent_key = str(run["agent_key"] or "")
    items: list[dict[str, Any]] = []
    if agent_key == "critic_qa_agent":
        for index, issue in enumerate(output.get("issues") or [], 1):
            if not isinstance(issue, dict):
                continue
            level = str(issue.get("level") or "").lower()
            target_id = str(issue.get("target_id") or f"issue:{index}")
            items.append({
                "stage": "qa",
                "entity_type": "lead" if not target_id.startswith("issue:") else "task",
                "entity_id": target_id,
                "decision": "reject" if level == "error" else ("warning" if level == "warning" else "needs_review"),
                "reason": text_value(issue.get("message"), "QA 发现需要人工复核的问题。"),
                "evidence": issue.get("evidence") or [],
                "confidence": output.get("confidence", run["confidence"]),
                "priority": level.upper()[:1],
                "risk": text_value(issue.get("suggested_action"), ""),
                "payload": {"backfilled": True, "issue": issue, "agent_run_id": int(run["id"])},
            })
    elif agent_key == "report_agent":
        for index, action in enumerate(output.get("recommended_actions") or [], 1):
            items.append({
                "stage": "report",
                "entity_type": "task",
                "entity_id": f"recommended_action:{index}",
                "decision": "recommended_action",
                "reason": text_value(action, "报告 Agent 生成的推荐动作。"),
                "evidence": output.get("summary") or "",
                "confidence": output.get("confidence", run["confidence"]),
                "payload": {"backfilled": True, "action": action, "agent_run_id": int(run["id"])},
            })
        for index, risk in enumerate(output.get("risks") or [], 1):
            items.append({
                "stage": "report",
                "entity_type": "task",
                "entity_id": f"risk:{index}",
                "decision": "risk_warning",
                "reason": text_value(risk, "报告 Agent 生成的风险提示。"),
                "confidence": output.get("confidence", run["confidence"]),
                "risk": text_value(risk, ""),
                "payload": {"backfilled": True, "risk": risk, "agent_run_id": int(run["id"])},
            })
    elif agent_key == "reflection_evolution_agent":
        for item in output.get("agent_summaries") or []:
            if not isinstance(item, dict):
                continue
            target = str(item.get("agent_key") or item.get("agentKey") or item.get("agent") or item.get("agentName") or "agent")
            items.append({
                "stage": "reflection",
                "entity_type": "agent",
                "entity_id": target,
                "decision": "review",
                "reason": text_value(item.get("recommendation") or item.get("weaknesses") or item.get("strengths") or item, "复盘进化 Agent 生成的 Agent 表现总结。"),
                "evidence": item.get("evidence") or [],
                "confidence": output.get("confidence", run["confidence"]),
                "risk": text_value(item.get("risk"), ""),
                "payload": {"backfilled": True, "agent_summary": item, "agent_run_id": int(run["id"])},
            })
        for index, item in enumerate(output.get("prompt_improvements") or [], 1):
            if not isinstance(item, dict):
                continue
            target = str(item.get("agent_key") or item.get("agentKey") or "agent")
            items.append({
                "stage": "reflection",
                "entity_type": "agent",
                "entity_id": target,
                "decision": "prompt_candidate",
                "reason": text_value(item.get("title") or item.get("expected_gain"), f"Prompt 改进建议 {index}"),
                "evidence": item.get("prompt_patch") or "",
                "confidence": output.get("confidence", run["confidence"]),
                "risk": text_value(item.get("risk"), ""),
                "payload": {"backfilled": True, "prompt_improvement": item, "agent_run_id": int(run["id"])},
            })
        for index, case in enumerate(output.get("eval_cases") or [], 1):
            if not isinstance(case, dict):
                continue
            items.append({
                "stage": "reflection",
                "entity_type": "eval_case",
                "entity_id": str(case.get("id") or f"eval_case:{index}"),
                "decision": "generated",
                "reason": text_value(case.get("expected_result") or case.get("description"), "复盘进化 Agent 生成的评估样例。"),
                "evidence": case.get("evidence") or [],
                "confidence": output.get("confidence", run["confidence"]),
                "payload": {"backfilled": True, "eval_case": case, "agent_run_id": int(run["id"])},
            })
    return items


def task_ids(conn: sqlite3.Connection, requested: list[int]) -> list[int]:
    if requested:
        return requested
    return [int(row["id"]) for row in conn.execute("SELECT id FROM tasks ORDER BY id ASC").fetchall()]


def backfill(db_path: Path, requested_task_ids: list[int], dry_run: bool) -> int:
    storage = SalesStorage(db_path)
    inserted = 0
    with storage.connect() as conn:
        for task_id in task_ids(conn, requested_task_ids):
            runs = conn.execute(
                """
                SELECT * FROM agent_runs
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for run in runs:
                agent_key = str(run["agent_key"] or "")
                if agent_key not in AGENT_STAGE:
                    continue
                output = json_loads(run["output_summary"], {})
                if not isinstance(output, dict):
                    output = {"output": output}
                candidates = [stage_item(task_id, run, output), *extra_items(run, output)]
                for item in candidates:
                    if not exists(conn, task_id, item):
                        items.append(item)
            if items:
                print(f"task {task_id}: {'would insert' if dry_run else 'insert'} {len(items)} audit rows")
                if not dry_run:
                    storage.save_audit_decisions(task_id, items)
                    inserted += len(items)
            else:
                print(f"task {task_id}: no missing stage audit rows")
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill stage-level Harness audit rows from agent_runs.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to sales_leads.db")
    parser.add_argument("--task-id", type=int, action="append", default=[], help="Task id to backfill. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be inserted.")
    args = parser.parse_args()
    inserted = backfill(Path(args.db), args.task_id, args.dry_run)
    if not args.dry_run:
        print(f"inserted {inserted} audit rows")


if __name__ == "__main__":
    main()
