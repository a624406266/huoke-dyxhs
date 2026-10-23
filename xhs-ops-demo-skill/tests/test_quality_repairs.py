"""Regressions from task 4, using synthetic content and no model/platform calls."""
from __future__ import annotations
import tempfile
from pathlib import Path
import unittest

from test_keyword_pipeline import Adapter, Runner, note, plan
from test_service_demand import candidate, decision, FOCUSED
from agent_harness.offering import apply_offering_decision, inquiry_review
from agent_harness.pipeline import HarnessPipeline
from storage import SalesStorage
from workbench_store import WorkbenchStore, digest


class QualityRepairs(unittest.TestCase):
    def test_role_does_not_overwrite_nonbuyer_classification_or_reason(self):
        for role, category, text in [('discussion', 'irrelevant', '又热闹起来了'),
                                     ('supplier', 'information', '这种技术怎么调试')]:
            d = decision(intent_role=role, classification=category, reason='只按本人原文判断')
            out = apply_offering_decision(candidate(text), d, FOCUSED)
            self.assertEqual(out['classification'], category)
            self.assertEqual(out['intent_reason'], d['reason'])
            self.assertNotEqual(out['decision'], 'lead')

    def test_inquiry_order_does_not_upgrade_or_invent_evidence(self):
        for text, priority in [('定制智能体多少钱', 'high'), ('多少钱', 'medium'), ('想了解一下', 'medium'), ('dd', 'low')]:
            d = {'classification': 'uncertain', 'demand_origin': 'unclear'}
            metadata = inquiry_review({'content': text}, d)
            self.assertEqual(metadata['review_priority'], priority)
            self.assertNotIn('qualified', metadata)
            self.assertNotIn('evidence', metadata)
            self.assertTrue(metadata['missing_information'])

    def run_pipeline(self, adapter, runner=None, **options):
        return HarnessPipeline(adapter, {}, runner=runner or Runner()).run({'confirmedPlan': plan(maxNotes=1, **options)})

    def test_blank_search_summary_reads_detail_before_relevance_once(self):
        class Blank(Adapter):
            def search(self, *args, **kwargs):
                return {'data': {'items': [note('blank', title='', desc='')]}}
        adapter, runner = Blank(), Runner()
        result = self.run_pipeline(adapter, runner)
        sent = next(data for key, data in runner.calls if key == 'relevance_agent')
        self.assertEqual(sent['notes'][0]['desc'], '有适合通勤的推荐吗？')
        self.assertEqual(sum(call[0] == 'detail' for call in adapter.calls), 1)
        self.assertEqual(result['stageCoverage']['details']['completed'], 1)

    def test_image_only_source_is_unresolved_not_unrelated(self):
        class ImageOnly(Adapter):
            def search(self, *args, **kwargs):
                return {'data': {'items': [note('images', title='', desc='')]}}
            def read_note(self, note_id):
                return {'data': {'items': [note(note_id, title='', desc='')]}}
        result = self.run_pipeline(ImageOnly())
        self.assertEqual(result['notes'][0]['relevance']['decision'], 'needs_content')
        self.assertEqual(result['collectionSummary']['unresolvedNotes'], ['images'])
        self.assertTrue(result['leads']['leads'])  # Independent comments remain eligible for judgment.

    def test_refusal_to_outsource_changes_order_not_limits(self):
        low = {'title': '招开发', 'desc': '不接受外包，只接受坐班', 'relevance': {'collection_priority': 'high'}}
        high = {'title': '求定制', 'desc': '想找人报价', 'relevance': {'collection_priority': 'high'}}
        self.assertGreater(HarnessPipeline._collection_rank(low), HarnessPipeline._collection_rank(high))
        self.assertEqual(low['collection_priority'], 'low')

    def test_qa_missing_and_duplicate_ids_are_retried_then_counted_honestly(self):
        class MissingQA(Runner):
            def run(self, key, data, purpose=''):
                out = super().run(key, data, purpose)
                if key == 'critic_qa_agent':
                    row = {'target_id': data['leads'][0]['lead_id'], 'status': 'approved', 'reason': '原文支持'}
                    out['reviews'] = [row, row]  # Repeating it does not establish coverage.
                return out
        result = self.run_pipeline(Adapter(), MissingQA())
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['stageCoverage']['qa']['completed'], 0)
        self.assertTrue(all(x['qa_status'] == 'needs_review' for x in result['leads']['leads']))

    def test_business_confirmation_is_separate_from_qa_quality(self):
        p = HarnessPipeline(Adapter(), {}, runner=Runner())
        p._plan = FOCUSED
        lead = {'lead_id': 'a', 'evidenceComplete': True, 'classification': 'uncertain', 'preQaQualified': False, 'qualified': False}
        qa = {'approved': True, 'issues': [], 'reviews': [{'target_id': 'a', 'status': 'approved', 'reason': '待确认分类有依据'}],
              'review_requests': [{'target_id': 'a', 'reason': '是否本人需求未知', 'requested_action': '人工确认'}]}
        p._apply_qa([lead], qa)
        self.assertEqual(lead['qa_status'], 'approved')
        self.assertTrue(lead['business_review_required'])
        self.assertFalse(lead['qualified'])

    def test_global_qa_error_stays_failed_without_failing_neighbours(self):
        class GlobalQA(Runner):
            def run(self, key, data, purpose=''):
                out = super().run(key, data, purpose)
                if key == 'critic_qa_agent':
                    out.update(approved=False,
                        issues=[{'level': 'error', 'target_id': 'plan:scope', 'message': '任务级问题'}],
                        reviews=[{'target_id': lead['lead_id'], 'status': 'approved', 'reason': '原文支持'}
                                 for lead in data['leads']])
                return out
        result = self.run_pipeline(Adapter(), GlobalQA())
        self.assertFalse(result['qa']['approved'])
        self.assertTrue(all(lead['qa_status'] == 'approved' for lead in result['leads']['leads']))

    def test_pending_or_missing_qa_cannot_count_as_qualified(self):
        p = HarnessPipeline(Adapter(), {}, runner=Runner())
        p._plan = FOCUSED
        for status in ('missing', 'needs_review', 'rejected', 'approved'):
            lead = {'lead_id': 'a', 'evidenceComplete': True, 'classification': 'demand',
                    'preQaQualified': True, 'qualified': False, 'intent_reason': '原始需求理由'}
            reviews = [] if status == 'missing' else [{'target_id': 'a', 'status': status, 'reason': '逐条结果'}]
            p._apply_qa([lead], {'approved': True, 'issues': [], 'reviews': reviews})
            self.assertTrue(lead['preQaQualified'])
            self.assertEqual(lead['qualified'], status == 'approved')
            self.assertEqual(lead['intent_reason'], '原始需求理由')
            self.assertEqual(p._model_lead(lead)['qa_status'], lead['qa_status'])

    def test_specialized_human_review_can_reject_an_automatic_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkbenchStore(SalesStorage(Path(tmp) / 'test.db'))
            sample = {'id': 's', 'noteId': 'n', 'topic': 'qa', 'sourceUrl': 'https://example.test/n',
                'collectedAt': 1, 'text': '真实原文的测试占位', 'split': 'acceptance', 'annotator': 'fixture',
                'agentKey': 'critic_qa_agent', 'input': {'plan': {'offeringId': 'general'}, 'leads': [{'lead_id': 'a'}]},
                'expected': {'approved': False, 'issueTargets': ['a']}}
            dataset = store.import_dataset({'samples': [sample]})
            run = store.save_benchmark({'datasetId': dataset['datasetId'], 'promptHash': digest('fixture'),
                'agentKey': 'critic_qa_agent', 'autoPassed': True, 'rows': [{'id': 's'}], 'split': 'acceptance'})
            review = {'runId': run['runId'], 'reviewedSampleIds': ['s'], 'noFabrication': True,
                      'citationsVerified': True, 'note': '逐条核对'}
            with self.assertRaisesRegex(ValueError, 'qualityReviews'):
                store.review_benchmark(review, 0)
            result = store.review_benchmark({**review, 'qualityReviews': [{'sampleId': 's', 'passed': False, 'note': '错误归因'}]}, 0)
            self.assertFalse(result['passed'])
            self.assertTrue(result['qualityReviewComplete'])


if __name__ == '__main__':
    unittest.main()
