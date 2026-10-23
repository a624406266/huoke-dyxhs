"""Task-5 collection regressions: controlled platform responses, no external calls."""
from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import unittest

from test_keyword_pipeline import Adapter, Runner, note, plan
from agent_harness.pipeline import HarnessPipeline


NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()


def dated(identity, value=None, *, text=None):
    item = note(identity)
    if value is not None:
        item['note_card']['time'] = value
    if text is not None:
        item['note_card']['corner_tag_info'] = [{'type': 'publish_time', 'text': text}]
    return item


class TimeAdapter(Adapter):
    def __init__(self, items, details=None):
        super().__init__()
        self.items, self.details = items, details or {}

    def search(self, keyword, page=1, sort='popular'):
        self.calls.append(('search', keyword, page, sort))
        return {'data': {'items': copy.deepcopy(self.items), 'has_more': False}}

    def read_note(self, identity):
        self.calls.append(('detail', identity))
        data = self.details.get(identity, dated(identity))
        if isinstance(data, Exception):
            raise data
        return {'data': {'items': [copy.deepcopy(data)]}}


class TimeFilterPipelineTests(unittest.TestCase):
    def run_pipeline(self, adapter, **options):
        runner = Runner()
        pipeline = HarnessPipeline(adapter, {}, runner=runner)
        options = {'timeFilter': 'week', 'maxPagesPerKeyword': 1, 'maxNotes': 3, 'scanLeads': False, **options}
        with patch('agent_harness.pipeline.time.time', return_value=NOW), patch.object(pipeline, '_save_audit') as audit:
            result = pipeline.run({'confirmedPlan': plan(**options)})
        records = [row for call in audit.call_args_list for row in call.args[1]]
        return result, runner, records

    def test_unknown_date_reads_detail_before_filtering_and_only_once(self):
        adapter = TimeAdapter([dated('n')], {'n': dated('n', (NOW - 86400) * 1000)})
        result, runner, audits = self.run_pipeline(adapter)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([row['id'] for row in result['notes']], ['n'])
        self.assertEqual([row for row in adapter.calls if row[0] == 'detail'], [('detail', 'n')])
        self.assertEqual(result['collectionSummary']['timeResolution'], {'attempted': 1, 'resolved': 1, 'unresolved': 0, 'failed': 0, 'interrupted': 0})
        self.assertEqual(result['stageCoverage']['details']['completed'], 1)
        self.assertTrue(any(row['stage'] == 'time_filter' and row['decision'] == 'in_range' for row in audits))
        self.assertIn('relevance_agent', [key for key, _ in runner.calls])

    def test_relative_date_and_seconds_are_supported_without_detail(self):
        adapter = TimeAdapter([dated('relative', text='3天前'), dated('seconds', NOW - 86400)])
        result, _, _ = self.run_pipeline(adapter, readDetail=False)
        self.assertEqual({row['id'] for row in result['notes']}, {'relative', 'seconds'})
        self.assertFalse(any(row[0] == 'detail' for row in adapter.calls))
        self.assertEqual(result['collectionSummary']['timeReferenceAt'], NOW)

    def test_unresolved_date_is_partial_with_specific_audit_not_zero_demand(self):
        result, runner, audits = self.run_pipeline(TimeAdapter([dated('unknown')]))
        self.assertEqual(result['status'], 'partial')
        summary = result['collectionSummary']
        self.assertEqual(summary['filterCounts']['unknown_publish_time'], 1)
        self.assertEqual(summary['timeResolution']['unresolved'], 1)
        self.assertIn('待核验', summary['zeroResultReason'])
        self.assertEqual(result['notes'], [])
        self.assertEqual([key for key, _ in runner.calls], ['report_agent'])
        report_input = runner.calls[0][1]
        self.assertEqual(report_input['collectionSummary']['filterCounts']['unknown_publish_time'], 1)
        self.assertTrue(any(row['decision'] == 'unknown_publish_time' and row['entity_id'] == 'unknown' for row in audits))

    def test_known_old_note_is_a_valid_filtered_zero_without_extra_read(self):
        adapter = TimeAdapter([dated('old', (NOW - 8 * 86400) * 1000)])
        result, _, audits = self.run_pipeline(adapter)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['collectionSummary']['filterCounts']['outside_time_range'], 1)
        self.assertFalse(any(row[0] == 'detail' for row in adapter.calls))
        self.assertTrue(any(row['decision'] == 'outside_time_range' for row in audits))

    def test_disabled_detail_is_respected_and_unknown_dates_stay_unknown(self):
        adapter = TimeAdapter([dated('unknown')])
        result, _, _ = self.run_pipeline(adapter, readDetail=False)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['collectionSummary']['timeResolution']['attempted'], 0)
        self.assertFalse(any(row[0] == 'detail' for row in adapter.calls))

    def test_no_time_filter_keeps_undated_sources(self):
        adapter = TimeAdapter([dated('unknown')])
        result, _, _ = self.run_pipeline(adapter, timeFilter='all', readDetail=False)
        self.assertEqual(len(result['notes']), 1)
        self.assertEqual(result['collectionSummary']['filterCounts']['unknown_publish_time'], 0)
        self.assertEqual(result['collectionSummary']['timeResolution']['attempted'], 0)

    def test_filtered_sources_do_not_consume_final_note_budget(self):
        adapter = TimeAdapter([dated('old'), dated('recent')], {
            'old': dated('old', (NOW - 9 * 86400) * 1000), 'recent': dated('recent', NOW - 100)})
        result, _, _ = self.run_pipeline(adapter, maxNotes=1)
        self.assertEqual([row['id'] for row in result['notes']], ['recent'])
        self.assertEqual(result['collectionSummary']['timeResolution']['attempted'], 2)

    def test_duplicate_unknown_source_does_not_repeat_detail_requests(self):
        adapter = TimeAdapter([dated('unknown')])
        result, _, _ = self.run_pipeline(adapter, keywords=['a', 'b'])
        self.assertEqual(sum(row[0] == 'detail' for row in adapter.calls), 1)
        self.assertEqual(result['collectionSummary']['filterCounts']['unknown_publish_time'], 1)
        self.assertEqual(result['collectionSummary']['filterCounts']['duplicate'], 1)

    def test_all_returned_records_are_accounted_for_and_caps_still_hold(self):
        adapter = TimeAdapter([None, {'model_type': 'hot_query'}, {'model_type': 'note'},
            dated('old', NOW - 9 * 86400), dated('ok', NOW - 60), dated('ok', NOW - 60),
            *[dated(f'extra-{i}', NOW - 60) for i in range(8)]])
        result, _, _ = self.run_pipeline(adapter, maxNotes=1, readDetail=False)
        summary = result['collectionSummary']
        self.assertEqual(len(result['notes']), 1)
        self.assertEqual(sum(summary['filterCounts'].values()) + len(result['notes']), summary['receivedNotes'])
        for key in ('invalid_record', 'non_note', 'missing_note_id', 'outside_time_range', 'duplicate'):
            self.assertEqual(summary['filterCounts'][key], 1)
        self.assertEqual(summary['filterCounts']['capacity_limit'], 8)

    def test_detail_failure_preserves_good_sources_and_is_not_retried_per_keyword(self):
        adapter = TimeAdapter([dated('bad'), dated('good', NOW - 60)], {'bad': ValueError('详情不存在')})
        result, _, _ = self.run_pipeline(adapter)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([row['id'] for row in result['notes']], ['good'])
        self.assertEqual(result['collectionSummary']['timeResolution']['failed'], 1)
        self.assertTrue(any(row['stage'] == 'time_filter' and row['source'] == 'bad'
                            for row in result['collectionSummary']['failures']))

    def test_newer_snippet_cannot_override_older_date_revealed_by_detail(self):
        adapter = TimeAdapter([dated('changed', NOW - 60)], {'changed': dated('changed', NOW - 30 * 86400)})
        result, runner, audits = self.run_pipeline(adapter)
        self.assertEqual(result['notes'], [])
        self.assertFalse(result['leads']['leads'])
        self.assertEqual(result['collectionSummary']['filterCounts']['outside_time_range'], 1)
        self.assertEqual(result['collectionSummary']['unprocessedNotes'], 0)
        self.assertFalse(any(key in ('lead_signal_agent', 'critic_qa_agent') for key, _ in runner.calls))
        self.assertTrue(any(row['decision'] == 'outside_time_range' and '详情' in row['reason'] for row in audits))

    def test_captcha_stops_immediately_and_unprocessed_records_are_counted(self):
        adapter = TimeAdapter([dated('gate'), dated('untouched')], {'gate': RuntimeError('验证码 captcha')})
        result, runner, _ = self.run_pipeline(adapter)
        self.assertEqual(result['status'], 'needs_action')
        self.assertEqual([row for row in adapter.calls if row[0] == 'detail'], [('detail', 'gate')])
        self.assertFalse(runner.calls)
        self.assertEqual(result['collectionSummary']['timeResolution']['interrupted'], 1)
        self.assertEqual(result['collectionSummary']['unprocessedNotes'], 2)


if __name__ == '__main__':
    unittest.main()
