from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from source_normalization import extract_note, merge_note
from note_dates import CHINA_TZ, classify_note_time, normalize_note_dates, parse_note_date


NOW = datetime(2026, 10, 21, 15, 30, 0, tzinfo=CHINA_TZ).timestamp()


def item(**card):
    return {"id": "synthetic-id", "model_type": "note", "note_card": card}


class NoteDateTests(unittest.TestCase):
    def parse(self, value, **kwargs):
        return parse_note_date(value, now=kwargs.pop("now", NOW), **kwargs)

    def status(self, card, selected="week", now=NOW):
        return classify_note_time(normalize_note_dates(card, now=now), selected, now)["status"]

    def test_unix_seconds_milliseconds_and_numeric_strings_are_same_instant(self):
        expected = int((NOW - 3600) * 1000)
        for value in (expected, str(expected), expected / 1000, str(expected / 1000), f"{expected}e0"):
            with self.subTest(value=value):
                result = self.parse(value)
                self.assertEqual(result["status"], "known")
                self.assertEqual(result["timestampMs"], expected)
                self.assertEqual(result["raw"], value)

    def test_naive_and_offset_iso_datetimes_use_explicit_china_timezone(self):
        values = ("2026-10-21 12:30:00", "2026-10-21T12:30:00+08:00",
                  "2026-10-21T04:30:00Z", "2026-10-21T04:30:00+0000")
        expected = datetime(2026, 10, 21, 12, 30, tzinfo=CHINA_TZ).timestamp() * 1000
        for value in values:
            with self.subTest(value=value):
                parsed = self.parse(value)
                self.assertEqual(parsed["timestampMs"], expected)
                self.assertEqual(parsed["timezone"], "Asia/Shanghai")
        self.assertEqual(self.parse("2026-10-21T04:30:00.125Z")["timestampMs"], expected + 125)

    def test_date_and_minute_precision_are_preserved_as_intervals(self):
        day = self.parse("2026-10-20")
        self.assertEqual(day["precision"], "day")
        self.assertEqual(day["latestMs"] - day["earliestMs"], 86_399_999)
        minute = self.parse("2026-10-21 12:30")
        self.assertEqual(minute["precision"], "minute")
        self.assertEqual(minute["latestMs"] - minute["earliestMs"], 59_999)

    def test_relative_calendar_days_and_time_of_day(self):
        for label, date in (("今天", "2026-10-21"), ("昨天", "2026-10-20"), ("前天", "2026-10-19")):
            with self.subTest(label=label):
                parsed = normalize_note_dates({"publish_date": label}, now=NOW)
                self.assertEqual(parsed["publish_date"], date)
                self.assertEqual(parsed["dateEvidence"]["precision"], "day")
                self.assertEqual(classify_note_time(parsed, "week", NOW)["status"], "in_range")
        self.assertEqual(self.parse("昨天 23:45")["timestampMs"],
                         datetime(2026, 10, 20, 23, 45, tzinfo=CHINA_TZ).timestamp() * 1000)
        self.assertEqual(self.parse("前天 08:01:05")["precision"], "second")

    def test_relative_elapsed_units_preserve_rounding_uncertainty(self):
        for label, delta in (("3秒前", 3000), ("3 秒钟前", 3000), ("3分钟前", 180000),
                             ("3小时前", 10_800_000), ("3天前", 259_200_000)):
            with self.subTest(label=label):
                result = self.parse(label)
                self.assertEqual(result["status"], "known")
                self.assertEqual(result["latestMs"], NOW * 1000 - delta)
                self.assertEqual(self.status({"publish_date": label}), "in_range")

    def test_relative_date_is_frozen_and_not_reparsed_by_classification(self):
        note = normalize_note_dates({"publish_date": "3天前"}, now=NOW)
        before = copy.deepcopy(note)
        with patch("note_dates.time.time", side_effect=AssertionError("must use supplied now")):
            self.assertEqual(classify_note_time(note, "week", NOW)["status"], "in_range")
            self.assertEqual(classify_note_time(note, "week", NOW + 8 * 86400)["status"], "out_of_range")
        self.assertEqual(note, before)
        self.assertEqual(note["dateEvidence"]["referenceTimeMs"], NOW * 1000)

    def test_month_day_uses_latest_nonfuture_calendar_year(self):
        new_year = datetime(2026, 1, 2, 10, tzinfo=CHINA_TZ).timestamp()
        date = self.parse("12-31", now=new_year)
        self.assertEqual(date["timestampMs"], datetime(2025, 12, 31, tzinfo=CHINA_TZ).timestamp() * 1000)
        self.assertTrue(date["yearInferred"])
        self.assertEqual(self.parse("10-20")["timestampMs"],
                         datetime(2026, 10, 20, tzinfo=CHINA_TZ).timestamp() * 1000)
        self.assertEqual(self.parse("02-29")["timestampMs"],
                         datetime(2024, 2, 29, tzinfo=CHINA_TZ).timestamp() * 1000)
        self.assertEqual(self.parse("02-30")["status"], "unknown")

    def test_relative_day_follows_china_midnight_even_when_utc_date_differs(self):
        reference = datetime(2026, 10, 21, 1, tzinfo=CHINA_TZ).timestamp()
        self.assertEqual(datetime.fromtimestamp(reference, timezone.utc).day, 20)
        parsed = normalize_note_dates({"publish_date": "昨天"}, now=reference)
        self.assertEqual(parsed["publish_date"], "2026-10-20")

    def test_unknown_invalid_and_future_are_not_substituted_with_now(self):
        for value in (None, "", "未知", "2026-02-30", "10-99", "明天", 0, -1, True,
                      float("nan"), float("inf"), {}, [], "2026-10-22", "今天 23:59", NOW + 1,
                      "9" * 500 + "天前"):
            with self.subTest(value=value):
                result = self.parse(value)
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["timestampMs"])
        self.assertEqual(self.parse("2026-10-22")["reason"], "future_time")

    def test_date_boundaries_are_three_state_instead_of_invented_precision(self):
        threshold = NOW - 7 * 86400
        self.assertEqual(self.status({"time": threshold}), "in_range")
        self.assertEqual(self.status({"time": threshold - 1}), "out_of_range")
        self.assertEqual(self.status({"publish_date": "2026-10-14"}), "unknown")
        self.assertEqual(self.status({"publish_date": "7天前"}), "unknown")
        self.assertEqual(self.status({"publish_date": "8天前"}), "out_of_range")
        self.assertEqual(self.status({}), "unknown")
        self.assertEqual(self.status({}, "all"), "in_range")

    def test_updated_fields_and_edited_labels_never_imply_publication(self):
        cases = ({"last_update_time": int(NOW * 1000)}, {"updated_at": "今天"},
                 {"time": "编辑于 昨天"},
                 {"corner_tag_info": [{"type": "publish_time", "text": "编辑于 3天前"}]})
        for card in cases:
            with self.subTest(card=card):
                normalized = normalize_note_dates(card, now=NOW)
                self.assertEqual(normalized["publish_time_ms"], 0)
                self.assertEqual(normalized["dateEvidence"]["reason"], "update_only")
                self.assertEqual(normalized["updateDateEvidence"]["status"], "known")
                self.assertEqual(classify_note_time(normalized, "week", NOW)["status"], "unknown")

    def test_older_publication_wins_over_recent_update(self):
        card = {"time": int((NOW - 30 * 86400) * 1000), "last_update_time": str(int(NOW)),
                "corner_tag_info": [{"type": "publish_time", "text": "编辑于 今天"}]}
        parsed = normalize_note_dates(card, now=NOW)
        self.assertEqual(parsed["dateEvidence"]["source"], "note_card.time")
        self.assertEqual(parsed["updateDateEvidence"]["status"], "known")
        self.assertEqual(classify_note_time(parsed, "week", NOW)["status"], "out_of_range")

    def test_raw_source_and_diagnostics_are_preserved_without_mutating_input(self):
        card = {"time": "bad-time", "publish_date": "昨天",
                "corner_tag_info": [{"type": "publish_time", "text": "3天前"}]}
        before = copy.deepcopy(card)
        parsed = normalize_note_dates(card, now=NOW)
        self.assertEqual(card, before)
        self.assertEqual(parsed["rawTimeFields"], card)
        self.assertEqual(parsed["dateEvidence"]["source"], "note_card.publish_date")
        self.assertEqual(parsed["dateEvidence"]["raw"], "昨天")
        self.assertTrue(any(d["source"] == "note_card.time" and d["status"] == "unknown"
                            for d in parsed["dateEvidence"]["diagnostics"]))
        parsed["rawTimeFields"]["corner_tag_info"][0]["text"] = "changed"
        self.assertEqual(card, before)

    def test_extract_note_supports_frozen_reference_and_preserves_legacy_fields(self):
        with patch("note_dates.time.time", side_effect=AssertionError("clock drift")):
            note = extract_note(item(title="测试笔记", time=str(int(NOW - 86400))), now=NOW)
        self.assertEqual(note["publish_time_ms"], int((NOW - 86400) * 1000))
        self.assertEqual(note["publish_date"], "2026-10-20")
        self.assertEqual(note["dateEvidence"]["source"], "note_card.time")
        self.assertEqual(note["rawTimeFields"]["time"], str(int(NOW - 86400)))

    def test_extract_note_does_not_guess_id_timestamp(self):
        note = extract_note({"id": "68f700000000000000000000", "model_type": "note", "note_card": {}}, now=NOW)
        self.assertEqual(note["dateEvidence"]["status"], "unknown")
        self.assertEqual(note["publish_time_ms"], 0)

    def test_detail_publication_resolves_unknown_date(self):
        base = extract_note(item(), now=NOW)
        detail = extract_note(item(time=int((NOW - 86400) * 1000)), now=NOW)
        merged = merge_note(base, detail)
        self.assertEqual(classify_note_time(merged, "week", NOW)["status"], "in_range")
        self.assertEqual(merged["detailRawTimeFields"], detail["rawTimeFields"])
        self.assertEqual(base["dateEvidence"]["status"], "unknown")

    def test_detail_edit_cannot_overwrite_known_older_publication(self):
        base = extract_note(item(time=int((NOW - 30 * 86400) * 1000)), now=NOW)
        detail = extract_note(item(last_update_time=int(NOW * 1000), desc="更新正文"), now=NOW)
        merged = merge_note(base, detail)
        self.assertEqual(merged["dateEvidence"], base["dateEvidence"])
        self.assertEqual(merged["publish_time_ms"], base["publish_time_ms"])
        self.assertEqual(merged["updateDateEvidence"], detail["updateDateEvidence"])
        self.assertEqual(merged["desc"], "更新正文")
        self.assertEqual(classify_note_time(merged, "week", NOW)["status"], "out_of_range")

    def test_legacy_notes_are_normalized_and_bad_evidence_does_not_pass(self):
        self.assertEqual(classify_note_time({"publish_time_ms": str(int(NOW))}, "week", NOW)["status"], "in_range")
        self.assertEqual(classify_note_time({"publish_date": "昨天"}, "week", NOW)["status"], "in_range")
        self.assertEqual(classify_note_time({"publish_date": "编辑于 昨天"}, "week", NOW)["status"], "unknown")
        for evidence in ({"status": "known"}, {"status": "known", "earliestMs": "invalid", "latestMs": 0},
                         {"status": "known", "earliestMs": NOW * 1000, "latestMs": (NOW + 1) * 1000}):
            self.assertEqual(classify_note_time({"dateEvidence": evidence}, "week", NOW)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
