"""Side-effect-free source fields shared by collection adapters and the workbench."""
from __future__ import annotations

from typing import Any

from note_dates import normalize_note_dates
from note_images import image_sources


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
        except (ValueError, OverflowError):
            return 0
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return 0


def extract_note(item: dict[str, Any], rank: int = 0, *, now: float | None = None) -> dict[str, Any] | None:
    if item.get("model_type") != "note":
        return None
    card = item.get("note_card") or {}
    platform = str(item.get("platform") or card.get("platform") or "xhs").strip().lower() or "xhs"
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
    dates = normalize_note_dates(card, now=now)

    url = card.get("url") or item.get("url") or f"https://www.xiaohongshu.com/explore/{note_id}"
    if platform == "douyin" and not (card.get("url") or item.get("url")):
        url = f"https://www.douyin.com/video/{note_id}"

    return {
        "platform": platform,
        "rank": rank,
        "id": note_id,
        "post_id": item.get("post_id") or item.get("aweme_id") or note_id,
        "url": url,
        "title": title,
        "author": user.get("nickname") or user.get("nick_name") or "",
        "author_user_id": user.get("sec_user_id") or user.get("id") or user.get("user_id") or "",
        "liked": liked,
        "collected": collected,
        "comments": comments,
        "shared": shared,
        "engagement": liked + collected + comments + shared,
        "images": len(image_sources(card)),
        **({"image_sources": image_sources(card)} if "image_list" in card or "imageList" in card else {}),
        "tags": tags,
        "desc": desc,
        **dates,
    }


def merge_note(base: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return base
    merged = dict(base)
    # The detail gallery is authoritative, including an explicitly empty list.
    if "image_sources" in detail:
        merged["image_sources"] = detail["image_sources"]
        merged["images"] = detail["images"]
    for key in ["title", "author", "author_user_id", "desc"]:
        if detail.get(key):
            merged[key] = detail[key]
    detail_date = detail.get("dateEvidence")
    if isinstance(detail_date, dict) and detail_date.get("status") == "known":
        # Detail publication evidence can resolve a relative/unknown search
        # label; a recent edit alone cannot turn an older note into a new one.
        for key in ("publish_date", "publish_time_ms", "dateEvidence"):
            merged[key] = detail.get(key)
    elif not isinstance(merged.get("dateEvidence"), dict) or merged["dateEvidence"].get("status") != "known":
        merged["dateEvidence"] = detail_date
    if (detail.get("updateDateEvidence") or {}).get("status") == "known":
        merged["updateDateEvidence"] = detail["updateDateEvidence"]
    if detail.get("rawTimeFields"):
        merged["detailRawTimeFields"] = detail["rawTimeFields"]
    for key in ["liked", "collected", "comments", "shared", "images"]:
        if detail.get(key):
            merged[key] = detail[key]
    if detail.get("tags"):
        merged["tags"] = detail["tags"]
    merged["engagement"] = merged["liked"] + merged["collected"] + merged["comments"] + merged["shared"]
    return merged


def redact_text(value: str, keep: int = 2) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= keep:
        return value[0] + "*"
    return value[:keep] + "*" * min(4, max(1, len(value) - keep))
