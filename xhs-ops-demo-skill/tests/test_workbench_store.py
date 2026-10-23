import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from storage import SalesStorage
from workbench_store import WorkbenchStore, digest
from agent_harness.evaluator import HarnessEvaluator, binary_metrics
from agent_harness.planning import normalize_plan


class WorkbenchStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = SalesStorage(Path(self.tmp.name) / 'new.db')
        self.store = WorkbenchStore(self.storage)

    def test_plan_snapshot_and_template_do_not_share_mutable_data(self):
        plan = normalize_plan({'seedKeywords': ['书店'], 'maxNotes': 1234})
        saved = self.store.save_plan(plan)
        self.store.save_template('常用', plan)
        plan['keywords'].append('改变')
        self.assertEqual(self.store.get_plan(saved['planId'])['keywords'], ['书店'])
        self.assertEqual(self.store.templates()[0]['plan']['maxNotes'], 1234)
        with self.assertRaises(ValueError):
            self.store.get_plan('unknown')

    def test_review_survives_rerun_but_not_changed_evidence_or_goal(self):
        plan = normalize_plan({'seedKeywords': ['书店']})
        lead = {'lead_id': 'n:c', 'note_id': 'n', 'comment_id': 'c', 'content': '想开书店', 'evidence': ['想开书店']}
        result = {'leads': {'leads': [lead]}}
        task = self.storage.create_task({'confirmedPlan': plan}, sales_user_id=0)
        self.storage.update_task(task, result=result)
        self.store.review({'taskId': task, 'leadId': 'n:c', 'status': 'approved', 'note': '核对原文'}, 1)
        self.assertEqual(self.store.annotate(copy.deepcopy(result), plan)['leads']['leads'][0]['review']['status'], 'approved')
        changed = {**plan, 'searchGoal': '寻找合作机构'}
        self.assertEqual(self.store.annotate(copy.deepcopy(result), changed)['leads']['leads'][0]['review']['status'], 'pending')
        modified = copy.deepcopy(result)
        modified['leads']['leads'][0]['content'] = '广告已删除'
        self.assertEqual(self.store.annotate(modified, plan)['leads']['leads'][0]['review']['status'], 'pending')
        self.assertEqual(len(self.store.review_history('n:c')), 1)
        with self.assertRaises(ValueError):
            self.store.review({'taskId': task, 'leadId': 'n:c', 'status': 'approved', 'reviewBasis': 'stale'}, 1)

    def samples(self, count=100):
        return [{'id': str(i), 'noteId': str(i), 'topic': str(i % 3), 'sourceUrl': 'https://www.xiaohongshu.com/explore/' + str(i),
                 'collectedAt': '2026-10-19', 'text': '模拟测试样本，不作为真实验收证据', 'split': 'development' if i < 40 else 'acceptance',
                 'labels': {'relevant': True, 'classification': 'demand'}, 'annotator': 'unit-test', 'sourceVerified': True} for i in range(count)]

    def test_review_history_can_scope_same_lead_to_its_task(self):
        result = {'leads': {'leads': [{'lead_id': 'shared-lead', 'evidence': '隔离示例原文'}]}}
        first = self.storage.create_task({'confirmedPlan': {}}, sales_user_id=0)
        second = self.storage.create_task({'confirmedPlan': {}}, sales_user_id=0)
        for task, status in [(first, 'approved'), (second, 'rejected')]:
            self.storage.update_task(task, result=copy.deepcopy(result))
            self.store.review({'taskId': task, 'leadId': 'shared-lead', 'status': status, 'note': f'任务 {task}'}, 1)
        self.assertEqual([item['task_id'] for item in self.store.review_history('shared-lead')], [second, first])
        history = self.store.review_history('shared-lead', first)
        self.assertEqual([(item['task_id'], item['status']) for item in history], [(first, 'approved')])
        self.assertEqual(self.store.review_history('shared-lead', second)[0]['status'], 'rejected')
        self.assertEqual(self.store.review_history('shared-lead', second + 1000), [])
        self.assertEqual(self.store.review_history('missing-lead', first), [])
        for invalid in [0, -1, 1.5, True, '1 OR 1=1']:
            with self.subTest(task_id=invalid), self.assertRaises(ValueError):
                self.store.review_history('shared-lead', invalid)

    def test_dataset_freeze_group_leakage_and_duplicates(self):
        samples = self.samples()
        first = self.store.import_dataset({'samples': samples})
        self.assertEqual(first, self.store.import_dataset({'samples': samples}))
        bad = copy.deepcopy(samples)
        bad[50]['noteId'] = '1'
        with self.assertRaises(ValueError):
            self.store.import_dataset({'samples': bad})
        bad = copy.deepcopy(samples)
        bad[50]['id'] = '1'
        with self.assertRaises(ValueError):
            self.store.import_dataset({'samples': bad})

    def test_metrics_use_false_negatives_and_zero_predictions(self):
        rows = [{'labels': {'relevant': True}, 'relevant': False}, {'labels': {'relevant': False}, 'relevant': True}]
        self.assertEqual(binary_metrics(rows, 'relevant', 'relevant', True), {'tp': 0, 'fp': 1, 'fn': 1, 'precision': 0., 'recall': 0.})

    def test_failed_model_calls_are_failed_samples_not_quality_success(self):
        dataset = self.store.import_dataset({'samples': self.samples()})
        class Runner:
            def run(self, *args, **kwargs):
                raise RuntimeError('network unavailable')
        result = HarnessEvaluator(self.storage).run_dataset({}, {'datasetId': dataset['datasetId']}, lambda registry: Runner())
        self.assertEqual(result['metrics']['failedSamples'], 60)
        self.assertFalse(result['passed'])
        self.assertEqual(len(self.store.benchmarks()), 1)

    def test_no_operational_or_expected_gain_score_can_activate_prompt(self):
        self.assertIsNone(HarnessEvaluator(self.storage).evaluate_task(1)['score'])
        self.assertFalse(HarnessEvaluator(self.storage).consider_candidate(expected_gain=100)['promoted'])
        with self.assertRaises(ValueError):
            self.store.promotion_proof(1, 'prompt', 'missing')

    def test_promotion_bound_to_exact_prompt_and_frozen_acceptance(self):
        run = self.store.save_benchmark({'datasetId': 'fixture', 'promptId': 7, 'promptHash': digest('A'), 'passed': True, 'humanReviewComplete': True, 'split': 'acceptance'})
        self.assertTrue(self.store.promotion_proof(7, 'A', run['runId']))
        with self.assertRaises(ValueError):
            self.store.promotion_proof(7, 'modified', run['runId'])

    def test_automatic_scores_require_explicit_review_of_all_outputs(self):
        run = self.store.save_benchmark({'datasetId': 'fixture', 'promptId': 7, 'promptHash': digest('A'),
                'autoPassed': True, 'passed': False, 'split': 'acceptance', 'rows': [{'id': 'a'}, {'id': 'b'}]})
        with self.assertRaises(ValueError):
            self.store.promotion_proof(7, 'A', run['runId'])
        with self.assertRaises(ValueError):
            self.store.review_benchmark({'runId': run['runId'], 'reviewedSampleIds': ['a']}, 1)
        result = self.store.review_benchmark({'runId': run['runId'], 'reviewedSampleIds': ['a', 'b'],
                'note': 'fixture 模拟人工复核，仅测试状态机', 'noFabrication': True, 'citationsVerified': True}, 1)
        self.assertTrue(result['passed'])
        self.assertEqual(result['humanReview']['actor'], 1)
        self.assertTrue(self.store.promotion_proof(7, 'A', run['runId']))


if __name__ == '__main__':
    unittest.main()
