"""Publication-date evidence, parsed against an explicit China-time reference.

These helpers never use a note ID or an update time as a publication date. Date
labels have intervals instead of invented time-of-day precision; a filter
boundary inside an interval remains unknown until better evidence is available.
"""
from __future__ import annotations

import copy
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_DAY_MS = 86_400_000
_PUBLISHED_FIELDS = (
    "publish_time_ms", "publishTimeMs", "publish_time", "publishTime",
    "published_at", "publishedAt", "time", "create_time", "createTime",
    "created_at", "createdAt", "publish_date", "publishDate",
)
_UPDATED_FIELDS = (
    "last_update_time", "lastUpdateTime", "update_time", "updateTime",
    "updated_at", "updatedAt", "edit_time", "editTime",
)
_UPDATED_PREFIX = re.compile(r"^(?:最后\s*)?(?:编辑|更新|修改)\s*(?:于|时间)?\s*[:：]?\s*")
_PUBLISHED_PREFIX = re.compile(r"^(?:发布|发表于)\s*(?:于|时间)?\s*[:：]?\s*")


def _reference(now: float | None) -> tuple[float, datetime]:
    seconds = time.time() if now is None else float(now)
    if not math.isfinite(seconds):
        raise ValueError("日期参考时间必须是有效的 Unix 秒时间戳。")
    return seconds, datetime.fromtimestamp(seconds, CHINA_TZ)


def _unknown(source: str, raw: Any, now: float, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unknown", "timestampMs": None, "earliestMs": None, "latestMs": None,
            "source": source, "precision": "unknown", "reason": reason,
            "referenceTimeMs": int(now * 1000), "timezone": "Asia/Shanghai", "raw": copy.deepcopy(raw), **extra}


def _known(earliest: float, latest: float, *, source: str, raw: Any, now: float,
           precision: str, **extra: Any) -> dict[str, Any]:
    if not all(math.isfinite(v) for v in (earliest, latest)) or earliest <= 0 or latest < earliest:
        return _unknown(source, raw, now, "invalid_time")
    if earliest > now * 1000:
        return _unknown(source, raw, now, "future_time")
    # A day/minute label can include the still-unelapsed portion of the current
    # day/minute. Only its possible past portion is usable evidence.
    latest = min(latest, now * 1000)
    return {"status": "known", "timestampMs": int(earliest), "earliestMs": int(earliest),
            "latestMs": int(latest), "source": source, "precision": precision, "reason": "parsed",
            "referenceTimeMs": int(now * 1000), "timezone": "Asia/Shanghai", "raw": copy.deepcopy(raw), **extra}


def parse_note_date(value: Any, *, now: float | None = None, source: str = "date") -> dict[str, Any]:
    """Parse a *date value*; callers determine whether it means published/edited.

    Unix seconds/milliseconds and ISO offsets are absolute. Naive dates and
    relative calendar words use UTC+08:00 independently of the host timezone.
    """
    seconds, reference = _reference(now)
    if value is None or value == "" or isinstance(value, bool):
        return _unknown(source, value, seconds, "missing_time" if value in (None, "") else "invalid_time")
    text = str(value).strip()
    if len(text) > 128:
        return _unknown(source, value, seconds, "invalid_time")
    if isinstance(value, (int, float)) or re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
        try:
            numeric = float(value)
            # Contemporary Unix seconds have 10 digits, milliseconds 13.
            milliseconds = numeric if abs(numeric) >= 100_000_000_000 else numeric * 1000
            datetime.fromtimestamp(milliseconds / 1000, CHINA_TZ)
        except (ValueError, OverflowError, OSError):
            return _unknown(source, value, seconds, "invalid_time")
        return _known(milliseconds, milliseconds, source=source, raw=value, now=seconds,
                      precision="millisecond" if abs(numeric) >= 100_000_000_000 else "second")
    if not isinstance(value, str):
        return _unknown(source, value, seconds, "invalid_time")

    text = _UPDATED_PREFIX.sub("", _PUBLISHED_PREFIX.sub("", text)).strip()
    if text == "刚刚":
        return _known(seconds * 1000 - 59_999, seconds * 1000, source=source, raw=value,
                      now=seconds, precision="relative_minute")
    relative = re.fullmatch(r"(\d+)\s*(秒钟?|分钟?|小时|天)\s*前", text)
    if relative:
        count = int(relative.group(1))
        unit = relative.group(2)
        step, precision = (1000, "relative_second") if unit.startswith("秒") else (
            (60_000, "relative_minute") if unit.startswith("分") else (
                (3_600_000, "relative_hour") if unit == "小时" else (_DAY_MS, "relative_day")))
        # Relative labels round elapsed units. Preserve that uncertainty at the
        # requested window boundary instead of claiming an exact timestamp.
        return _known(seconds * 1000 - (count + 1) * step + 1, seconds * 1000 - count * step,
                      source=source, raw=value, now=seconds, precision=precision)

    calendar = re.fullmatch(r"(今天|昨天|前天)(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    inferred_year = False
    try:
        if calendar:
            day = (reference - timedelta(days={"今天": 0, "昨天": 1, "前天": 2}[calendar.group(1)])).date()
            if calendar.group(2):
                parsed = datetime(day.year, day.month, day.day, int(calendar.group(2)),
                                  int(calendar.group(3)), int(calendar.group(4) or 0), tzinfo=CHINA_TZ)
                precision = "second" if calendar.group(4) else "minute"
            else:
                parsed, precision = datetime(day.year, day.month, day.day, tzinfo=CHINA_TZ), "day"
        else:
            month_day = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
            if month_day:
                month, day = int(month_day.group(1)), int(month_day.group(2))
                # Latest valid calendar occurrence, including leap-day labels.
                candidates = []
                for year in range(reference.year, reference.year - 5, -1):
                    try:
                        candidate = datetime(year, month, day, tzinfo=CHINA_TZ)
                        if candidate.date() <= reference.date():
                            candidates.append(candidate)
                    except ValueError:
                        continue
                if not candidates:
                    return _unknown(source, value, seconds, "invalid_time")
                parsed, precision, inferred_year = candidates[0], "day", True
                if month_day.group(3):
                    parsed = parsed.replace(hour=int(month_day.group(3)), minute=int(month_day.group(4)),
                                            second=int(month_day.group(5) or 0))
                    precision = "second" if month_day.group(5) else "minute"
            else:
                normalized = re.sub(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", r"\1-\2-\3", text)
                normalized = normalized.replace("/", "-")
                full_date = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
                if full_date:
                    parsed = datetime(*(int(part) for part in full_date.groups()), tzinfo=CHINA_TZ)
                    precision = "day"
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?", normalized):
                    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=CHINA_TZ)
                    time_component = re.split(r"[T ]", normalized, maxsplit=1)[1]
                    precision = "millisecond" if "." in time_component else (
                        "second" if re.match(r"\d{2}:\d{2}:\d{2}", time_component) else "minute")
                else:
                    return _unknown(source, value, seconds, "unrecognized_time")
        earliest = parsed.timestamp() * 1000
        span = {"day": _DAY_MS - 1, "minute": 59_999}.get(precision, 0)
        return _known(earliest, earliest + span, source=source, raw=value, now=seconds,
                      precision=precision, yearInferred=inferred_year)
    except (ValueError, OverflowError, OSError):
        return _unknown(source, value, seconds, "invalid_time")


def normalize_note_dates(card: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Return compatible display fields plus publication/update provenance."""
    seconds, _ = _reference(now)
    raw_fields: dict[str, Any] = {}
    published: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    for key in (*_PUBLISHED_FIELDS, *_UPDATED_FIELDS):
        if key not in card:
            continue
        raw_fields[key] = copy.deepcopy(card[key])
        value = card[key]
        is_update = key in _UPDATED_FIELDS or (isinstance(value, str) and bool(_UPDATED_PREFIX.match(value.strip())))
        evidence = parse_note_date(value, now=seconds, source=f"note_card.{key}")
        (updated if is_update else published).append(evidence)
    for tags_key in ("corner_tag_info", "cornerTagInfo"):
        tags = card.get(tags_key)
        if not isinstance(tags, list):
            continue
        raw_tags = []
        for index, tag in enumerate(tags):
            if not isinstance(tag, dict):
                continue
            tag_type, value = tag.get("type"), tag.get("text")
            edited = isinstance(value, str) and bool(_UPDATED_PREFIX.match(value.strip()))
            if tag_type not in {"publish_time", "publishTime", "update_time", "edit_time"} and not edited:
                continue
            raw_tags.append({"type": tag_type, "text": copy.deepcopy(value)})
            evidence = parse_note_date(value, now=seconds, source=f"note_card.{tags_key}[{index}].text")
            (updated if edited or tag_type in {"update_time", "edit_time"} else published).append(evidence)
        if raw_tags:
            raw_fields[tags_key] = raw_tags

    def select(evidence: list[dict[str, Any]], missing_reason: str) -> dict[str, Any]:
        chosen = next((entry for entry in evidence if entry["status"] == "known"), None)
        if chosen is None:
            chosen = evidence[0] if evidence else _unknown("", None, seconds, missing_reason)
        result = copy.deepcopy(chosen)
        result["diagnostics"] = [{"source": entry["source"], "status": entry["status"],
                                  "reason": entry["reason"], "precision": entry["precision"]}
                                 for entry in evidence]
        return result

    publication = select(published, "update_only" if updated else "missing_time")
    update = select(updated, "missing_time")
    stamp = publication.get("timestampMs") or 0
    return {"publish_time_ms": stamp,
            "publish_date": datetime.fromtimestamp(stamp / 1000, CHINA_TZ).strftime("%Y-%m-%d") if stamp else "",
            "dateEvidence": publication, "updateDateEvidence": update, "rawTimeFields": raw_fields}


def classify_note_time(note: dict[str, Any], selected: str, now: float) -> dict[str, str]:
    """Three-state decision; never reparse a normalized relative date label."""
    seconds, _ = _reference(now)
    if selected == "all":
        return {"status": "in_range", "reason": "unrestricted"}
    days = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}.get(selected)
    if days is None:
        return {"status": "unknown", "reason": "unsupported_time_filter"}
    evidence = note.get("dateEvidence")
    if not isinstance(evidence, dict):
        date_fields = {key: note[key] for key in ("publish_time_ms", "publish_date") if note.get(key)}
        evidence = normalize_note_dates(date_fields, now=seconds)["dateEvidence"]
    if evidence.get("status") != "known":
        return {"status": "unknown", "reason": str(evidence.get("reason") or "missing_time")}
    try:
        earliest, latest = float(evidence["earliestMs"]), float(evidence["latestMs"])
    except (TypeError, ValueError, KeyError):
        return {"status": "unknown", "reason": "invalid_time"}
    if not all(math.isfinite(v) for v in (earliest, latest)) or earliest <= 0 or latest < earliest:
        return {"status": "unknown", "reason": "invalid_time"}
    if earliest > seconds * 1000 or latest > seconds * 1000:
        return {"status": "unknown", "reason": "future_time"}
    threshold = seconds * 1000 - days * _DAY_MS
    if latest < threshold:
        return {"status": "out_of_range", "reason": "older_than_window"}
    if earliest >= threshold:
        return {"status": "in_range", "reason": "within_window"}
    return {"status": "unknown", "reason": "boundary_precision"}
