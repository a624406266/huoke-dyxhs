from __future__ import annotations

import time
from typing import Any


class HarnessEvaluator:
    def __init__(self, storage: Any | None = None) -> None:
        self.storage = storage

    def evaluate_task(self, task_id: int) -> dict[str, Any]:
        runs = self.storage.list_agent_runs(task_id) if self.storage else []
        total = max(1, len(runs))
        failed = sum(1 for run in runs if run.get("error"))
        low_conf = sum(1 for run in runs if float(run.get("confidence") or 0) < 0.55)
        evidence_missing = sum(1 for run in runs if not _has_evidence(run.get("outputSummary", "")))
        json_success = 1.0 - failed / total
        confidence_score = 1.0 - low_conf / total
        evidence_score = 1.0 - evidence_missing / total
        safety_score = 1.0 if failed == 0 else 0.7
        score = round((json_success * 0.35) + (confidence_score * 0.25) + (evidence_score * 0.25) + (safety_score * 0.15), 4)
        result = {
            "taskId": task_id,
            "score": score,
            "jsonSuccess": round(json_success, 4),
            "confidenceScore": round(confidence_score, 4),
            "evidenceScore": round(evidence_score, 4),
            "safetyScore": round(safety_score, 4),
            "runs": total,
            "failedRuns": failed,
            "createdAt": time.time(),
        }
        if self.storage:
            self.storage.save_eval_run(task_id, "task-quality", "all-agents", score, result)
        return result

    def consider_candidate(
        self,
        *,
        task_id: int,
        agent_key: str,
        candidate_prompt_id: int,
        active_score: float,
        expected_gain: float,
        has_safety_risk: bool,
    ) -> dict[str, Any]:
        task_eval = self.evaluate_task(task_id)
        candidate_score = min(0.99, max(0.0, task_eval["score"] + expected_gain))
        should_promote = (
            candidate_score >= 0.85
            and candidate_score - float(active_score or 0.0) >= 0.03
            and not has_safety_risk
        )
        result = {
            "taskId": task_id,
            "agentKey": agent_key,
            "candidatePromptId": candidate_prompt_id,
            "activeScore": round(float(active_score or 0.0), 4),
            "candidateScore": round(candidate_score, 4),
            "promoted": bool(should_promote),
            "reason": "score-threshold-met" if should_promote else "kept-as-candidate",
            "safetyRisk": bool(has_safety_risk),
        }
        if self.storage:
            self.storage.update_prompt_score(candidate_prompt_id, result["candidateScore"])
            self.storage.save_eval_run(task_id, "prompt-candidate", agent_key, result["candidateScore"], result)
            if should_promote:
                self.storage.promote_prompt_version(candidate_prompt_id)
        return result


def _has_evidence(text: str) -> bool:
    if isinstance(text, dict):
        evidence = text.get("evidence")
        return bool(evidence)
    if isinstance(text, list):
        return bool(text)
    return '"evidence"' in str(text) or "证据" in str(text)
