"""Operational statistics are not accuracy. Quality requires a frozen labeled set."""
from __future__ import annotations
import time
import copy
import json
from typing import Any
from workbench_store import WorkbenchStore, digest
from .registry import PromptRegistry
from .runtime import StructuredAgentRunner, redact_external, validate_agent_business_output
from .offering import GENERAL, SERVICE_GOAL, SERVICE_POLICY_VERSION, apply_offering_decision, focused, normalize_offering


def binary_metrics(rows: list, label: str, prediction: str, positive: Any) -> dict:
    tp = sum(r['labels'][label] == positive and r.get(prediction) == positive for r in rows)
    fp = sum(r['labels'][label] != positive and r.get(prediction) == positive for r in rows)
    fn = sum(r['labels'][label] == positive and r.get(prediction) != positive for r in rows)
    return {'precision': tp / (tp + fp) if tp + fp else 0.0,
            'recall': tp / (tp + fn) if tp + fn else 0.0, 'tp': tp, 'fp': fp, 'fn': fn}


class HarnessEvaluator:
    def __init__(self, storage: Any | None = None) -> None:
        self.storage = storage

    def evaluate_task(self, task_id: int) -> dict:
        runs = self.storage.list_agent_runs(task_id) if self.storage else []
        failed = sum(bool(run.get('error')) for run in runs)
        return {'taskId': task_id, 'kind': 'operational', 'runs': len(runs), 'failedRuns': failed,
                'executionSuccessRate': (len(runs) - failed) / len(runs) if runs else None,
                'score': None, 'qualityEvaluated': False, 'createdAt': time.time(),
                'message': '执行成功率不代表判断准确率；质量需运行人工标注固定集。'}

    def consider_candidate(self, **kwargs: Any) -> dict:
        return {'candidatePromptId': kwargs.get('candidate_prompt_id'), 'promoted': False,
                'candidateScore': None, 'reason': 'requires-frozen-dataset-and-admin-activation'}

    def run_dataset(self, config: dict, payload: dict, runner_factory=None) -> dict:
        config = copy.deepcopy(config)
        store = WorkbenchStore(self.storage)
        dataset_id = str(payload.get('datasetId') or '')
        samples = store.dataset(dataset_id)
        split = str(payload.get('split') or 'acceptance')
        if split not in {'development', 'acceptance'}:
            raise ValueError('split 必须是 development 或 acceptance。')
        registry = PromptRegistry(self.storage)
        registry.ensure_defaults()
        prompt_id = int(payload.get('promptId') or 0)
        candidate = next((p for p in registry.prompt_versions() if int(p.get('id') or 0) == prompt_id), None) if prompt_id else None
        if prompt_id and not candidate:
            raise ValueError('Prompt 不存在。')
        supported = {'relevance_agent', 'lead_signal_agent', 'critic_qa_agent', 'report_agent'}
        if candidate and candidate['agentKey'] not in supported:
            raise ValueError('此 Agent 尚无专用验收方式，不能用其他 Agent 的成绩代替。')
        agent_key = candidate['agentKey'] if candidate else str(payload.get('agentKey') or 'lead_signal_agent')
        if agent_key not in supported:
            raise ValueError('不支持的评测 Agent。')
        specialised = agent_key in {'critic_qa_agent', 'report_agent'}
        eligible = [s for s in samples if (s.get('agentKey') == agent_key if specialised
                    else s.get('agentKey') in {None, '', 'relevance_agent', 'lead_signal_agent'})]
        scope = ((candidate or {}).get('metrics') or {}).get('learningContext') or {}
        if scope.get('offeringId'):
            eligible = [s for s in eligible if normalize_offering(s.get('offeringId', GENERAL)) == scope['offeringId']]
        if candidate:
            active_prompt = registry.active_prompt
            registry.active_prompt = lambda key: candidate if key == candidate['agentKey'] else active_prompt(key)
        keys = (agent_key,) if specialised else ('relevance_agent', 'lead_signal_agent')
        versions = {k: registry.active_prompt(k) for k in keys}
        # Concurrent admin changes must not change the prompts during a frozen run.
        registry.active_prompt = lambda key: versions[key]
        history = copy.deepcopy(registry.prompt_versions())
        registry.prompt_versions = lambda agent_key=None: copy.deepcopy(
            [row for row in history if not agent_key or row.get('agentKey') == agent_key])
        runner = runner_factory(registry) if runner_factory else StructuredAgentRunner(config, registry=registry)
        rows = []
        selected_samples = [sample for sample in eligible if sample['split'] == split]
        effective = {offering: {key: copy.deepcopy(registry.effective_prompt(key, {'offeringId': offering}))
                               for key in keys}
                     for offering in {normalize_offering(s.get('offeringId', GENERAL)) for s in selected_samples}}
        prompt_hash = digest(candidate['prompt'] if candidate else
                             {scope: {key: row['prompt'] for key, row in prompts.items()} for scope, prompts in effective.items()})
        offering_counts: dict[str, int] = {}
        for sample in selected_samples:
            offering = normalize_offering(sample.get('offeringId', GENERAL))
            offering_counts[offering] = offering_counts.get(offering, 0) + 1
            row = {'id': sample['id'], 'labels': sample.get('labels', {}), 'sourceUrl': sample['sourceUrl'],
                   'offeringId': offering, 'error': '', 'inputText': redact_external(sample['text'])}
            try:
                frozen_input = copy.deepcopy(sample.get('input') or {})
                plan = copy.deepcopy(frozen_input.get('confirmedPlan') or frozen_input.get('plan') or {})
                if plan.get('offeringId', offering) != offering:
                    raise ValueError('冻结输入计划与样本 offeringId 不一致。')
                context = {**plan, 'offeringId': offering,
                           'keywords': sample.get('keywords') or plan.get('keywords') or [sample['topic']]}
                context['searchGoal'] = sample.get('searchGoal') or (
                    plan.get('searchGoal') or (SERVICE_GOAL if focused(context) else '识别相关真实需求和讨论'))
                if specialised:
                    request = {**frozen_input, 'plan': context, 'confirmedPlan': context,
                               'offeringId': offering, 'searchGoal': context['searchGoal']}
                    row['inputContext'] = redact_external(request)
                    prediction = runner.run(agent_key, request, purpose='专用固定集质量评测')
                    validate_agent_business_output(agent_key, prediction)
                    row['prediction'] = redact_external(prediction)
                    row['expected'] = sample['expected']
                    row['machineChecksPassed'] = self._specialised_check(agent_key, prediction, sample['expected'], request)
                    # These need a human comparing the complete output to its frozen input.
                    row['citationPassed'] = None
                    row['fabricationPassed'] = None
                    rows.append(row)
                    continue
                row['inputContext'] = redact_external({**frozen_input, **context})
                rel = runner.run('relevance_agent', {**context, 'notes': [{'note_id': sample['id'], 'content': sample['text']}]}, purpose='固定集相关性评测')
                sig = runner.run('lead_signal_agent', {**frozen_input, **context,
                    'leadCandidates': [{'candidate_id': sample['id'], 'note_id': sample['noteId'], 'content': sample['text']}]}, purpose='固定集需求分类评测')
                r = next(x for x in rel.get('notes', []) if x.get('note_id') == sample['id'])
                s = next(x for x in sig.get('leads', []) if x.get('candidate_id') == sample['id'])
                s = apply_offering_decision({'content': sample['text']}, s, context)
                row.update(relevant=r.get('decision') == 'keep', classification=s.get('classification'), evidence=s.get('evidence') or [])
                evidence = row['evidence'] if isinstance(row['evidence'], list) else [row['evidence']]
                row['citationPassed'] = bool(evidence) and all(isinstance(e, str) and e and e in str(redact_external(sample['text'])) for e in evidence)
                forbidden = {'identity', 'budget', 'contact', 'phone', 'email', 'price', 'commitment'}
                row['fabricationPassed'] = not any(s.get(k) for k in forbidden)
                row['prediction'] = redact_external(s)
            except Exception as exc:
                row.update(error=str(redact_external(str(exc))), relevant=None, classification=None,
                           citationPassed=False, fabricationPassed=False, machineChecksPassed=False)
            rows.append(row)
        if not rows:
            raise ValueError('待准备：所选 Agent、服务模式和 split 没有人工冻结样本；复盘建议不能代替验收集。')
        metrics = ({'machineChecksPassed': sum(r.get('machineChecksPassed') is True for r in rows),
                    'failedSamples': sum(bool(r['error']) for r in rows),
                    'manualQualityReviewRequired': len(rows)} if specialised else
                   {'relevance': binary_metrics(rows, 'relevant', 'relevant', True),
                   'demand': binary_metrics(rows, 'classification', 'classification', 'demand'),
                   'citationsPassed': sum(r['citationPassed'] for r in rows),
                   'fabricationChecksPassed': sum(r['fabricationPassed'] for r in rows),
                   'failedSamples': sum(bool(r['error']) for r in rows)})
        requirements = {'atLeast100Samples': len(eligible) >= 100,
                        'development40': sum(s['split'] == 'development' for s in eligible) >= 40,
                        'acceptance60': sum(s['split'] == 'acceptance' for s in eligible) >= 60,
                        'atLeast3Topics': len({s['topic'] for s in eligible}) >= 3,
                        'sourcesHumanVerified': all(s.get('sourceVerified') is True for s in eligible),
                        'acceptanceRun': split == 'acceptance'}
        quality = (metrics['machineChecksPassed'] == len(rows) and metrics['failedSamples'] == 0) if specialised else (
                   metrics['relevance']['precision'] >= .85 and metrics['relevance']['recall'] >= .8
                   and metrics['demand']['precision'] >= .8 and metrics['demand']['recall'] >= .7
                   and metrics['citationsPassed'] == len(rows) and metrics['fabricationChecksPassed'] == len(rows)
                   and metrics['failedSamples'] == 0)
        return store.save_benchmark({'datasetId': dataset_id, 'promptId': prompt_id or None, 'promptHash': prompt_hash,
                  'agentKey': agent_key, 'evaluationKind': ('qa' if agent_key == 'critic_qa_agent' else 'report') if specialised else 'classification',
                  'promptVersions': {k: v.get('version') for k, v in versions.items()},
                  'effectivePromptVersions': {scope: {key: {'id': row.get('id'), 'version': row.get('version'),
                        'promptHash': digest(row['prompt'])} for key, row in prompts.items()}
                        for scope, prompts in effective.items()},
                  'servicePolicyVersion': SERVICE_POLICY_VERSION if any(focused(s) for s in selected_samples) else None,
                  'offeringCounts': offering_counts,
                  'model': (config.get('llm') or {}).get('model'), 'split': split,
                  'metrics': metrics, 'requirements': requirements, 'autoPassed': quality and all(requirements.values()),
                  'passed': False, 'humanReviewComplete': False,
                  'qualityReviewComplete': False,
                  'requiredHumanChecks': ['来源及引用支持', '无虚构身份预算或成交', '逐条判断与任务范围一致']
                                         + (['QA 问题归因与严重度正确'] if agent_key == 'critic_qa_agent'
                                            else ['报告覆盖、统计、局限及建议忠实'] if agent_key == 'report_agent' else []),
                  'rows': rows, 'limitations': ['来源核验由标注人提供；自动检查不能代替逐条人工质量验收。',
                      'QA 的目标匹配、报告的事实文本匹配只检验固定约束，不证明语义正确；必须逐条审阅完整输入和输出。']})

    @staticmethod
    def _specialised_check(agent_key: str, prediction: dict, expected: dict, frozen_input: dict | None = None) -> bool:
        if not isinstance(expected, dict):
            raise ValueError('专用评测缺少人工 expected 标签。')
        if agent_key == 'critic_qa_agent':
            targets = expected.get('issueTargets')
            if type(expected.get('approved')) is not bool or not isinstance(targets, list):
                raise ValueError('QA 样本需人工 approved 布尔值和 issueTargets 数组。')
            sources = [str(lead.get('lead_id') or '') for lead in (frozen_input or {}).get('leads', []) if isinstance(lead, dict)]
            reviews = prediction.get('reviews')
            if (not sources or '' in sources or len(sources) != len(set(sources))
                    or not isinstance(reviews, list) or any(not isinstance(row, dict) for row in reviews)):
                return False
            reviewed = [str(row.get('target_id') or '') for row in reviews]
            if len(reviewed) != len(sources) or set(reviewed) != set(sources):
                return False
            if prediction['approved'] and any(row.get('status') != 'approved' for row in reviews):
                return False
            return (prediction['approved'] == expected['approved']
                    and {issue['target_id'] for issue in prediction['issues']} == set(targets))
        required = expected.get('requiredFacts')
        forbidden = expected.get('forbiddenClaims')
        if (not isinstance(required, list) or not required or not isinstance(forbidden, list)
                or any(not isinstance(v, str) or not v.strip() for v in [*required, *forbidden])):
            raise ValueError('报告样本需非空 requiredFacts 及 forbiddenClaims 字符串数组。')
        output = json.dumps(prediction, ensure_ascii=False)
        return all(fact in output for fact in required) and not any(claim in output for claim in forbidden)
