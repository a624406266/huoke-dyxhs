"""Bounded public note-image reads and source-preserving evidence helpers."""
from __future__ import annotations

import base64
import hashlib
import io
import re
import time
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGES_PER_NOTE = 20
IMAGE_HOSTS = {
    "xhs": ("xhscdn.com",),
    "douyin": ("douyincdn.com", "byteimg.com", "ibytedtos.com", "pstatp.com"),
}


def image_sources(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep gallery order, including images whose URL is missing. Never use avatars/covers."""
    rows = card.get("image_list", card.get("imageList", []))
    if not isinstance(rows, list):
        return []
    sources = []
    for index, image in enumerate(rows, 1):
        image = image if isinstance(image, dict) else {"url": image} if isinstance(image, str) else {}
        info = image.get("info_list", image.get("infoList", []))
        info = [row for row in info if isinstance(row, dict)] if isinstance(info, list) else []
        preferred = next((row.get("url") for row in info
                          if row.get("image_scene", row.get("imageScene")) == "WB_DFT"), None)
        url = image.get("url_default") or image.get("urlDefault") or preferred or image.get("url")
        if not url:
            url = next((row.get("url") for row in info if row.get("url")), "")
        sources.append({"index": index, "url": url if isinstance(url, str) else ""})
    return sources


def checked_image_url(url: str, platform: str) -> str:
    """Only the platform's public image CDN can be read, including every redirect."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443} or not parsed.path
                or not any(host == suffix or host.endswith("." + suffix) for suffix in IMAGE_HOSTS.get(platform, ()))):
            raise ValueError
        # Search/detail APIs sometimes return HTTP CDN links; never send an insecure request.
        return urlunsplit(("https", host, parsed.path, parsed.query, ""))
    except (ValueError, TypeError):
        raise ValueError("图片缺少有效的平台 CDN 地址，未下载。") from None


def fetch_note_image(url: str, platform: str, *, client: Any = None, stop_check: Any = None) -> dict[str, Any]:
    """Download without account cookies or model credentials; retain bytes only in memory."""
    import httpx
    from PIL import Image

    current = checked_image_url(url, platform)
    check = stop_check or (lambda: None)
    started = time.monotonic()
    owned = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False)
    try:
        for _ in range(4):
            check()
            with client.stream("GET", current, headers={
                "Referer": "https://www.xiaohongshu.com/" if platform == "xhs" else "https://www.douyin.com/",
                "User-Agent": "Mozilla/5.0", "Accept": "image/*",
            }) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    current = checked_image_url(urljoin(current, response.headers.get("location", "")), platform)
                    continue
                if response.status_code != 200:
                    raise ValueError(f"图片下载失败（HTTP {response.status_code}），需重新读取来源或人工核验。")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > MAX_IMAGE_BYTES:
                    raise ValueError("图片超过 8 MiB 读取上限，未送入模型。")
                body = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    check()
                    if time.monotonic() - started > 30:
                        raise ValueError("图片下载超过 30 秒，已停止读取。")
                    body.extend(chunk)
                    if len(body) > MAX_IMAGE_BYTES:
                        raise ValueError("图片超过 8 MiB 读取上限，未送入模型。")
                break
        else:
            raise ValueError("图片重定向次数超过上限，未送入模型。")
        try:
            with Image.open(io.BytesIO(body)) as image:
                mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}.get(image.format)
                width, height = image.size
                if not mime or width * height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError
                image.verify()
        except Exception:
            raise ValueError("图片格式无效、为动图或分辨率超过限制，需人工查看。") from None
        return {"data_uri": f"data:{mime};base64," + base64.b64encode(body).decode("ascii"),
                "sha256": hashlib.sha256(body).hexdigest(), "mime": mime,
                "bytes": len(body), "width": width, "height": height}
    except httpx.RequestError:
        raise ValueError("图片下载网络失败，图片内容尚未核验。") from None
    finally:
        if owned:
            client.close()


def substantive_text(value: Any) -> str:
    return re.sub(r"#[^#]*\[话题\]#|#[^\s#]+#?", "", str(value or "")).strip(" #\t\r\n")


def note_content(note: dict[str, Any]) -> str:
    """Quotable source text, with image transcription clearly separated from original body."""
    parts = [str(note.get("title") or ""), str(note.get("desc") or "")]
    tags = note.get("tags") or []
    if tags:
        parts.append("标签：" + " / ".join(str(tag) for tag in tags))
    for row in note.get("image_evidence") or []:
        if row.get("text"):
            parts.append(f"【图片 {row['index']} 识别文字】\n{row['text']}")
    return "\n".join(part for part in parts if part)


def missing_note_content(note: dict[str, Any]) -> bool:
    if note.get("read_error"):
        return True
    if note.get("images"):
        return note.get("image_status") != "complete"
    return not bool(substantive_text(note.get("desc")))


def validate_image_output(output: dict[str, Any]) -> None:
    if not isinstance(output, dict) or output.get("status") not in {"recognized", "no_text", "partial", "unreadable"}:
        raise ValueError("图片识别缺少有效 status。")
    for field, limit in (("text", 20000), ("visual_description", 2000)):
        if not isinstance(output.get(field), str) or len(output[field]) > limit:
            raise ValueError(f"图片识别 {field} 格式无效或超过长度上限。")
    uncertain = output.get("uncertain_regions")
    if not isinstance(uncertain, list) or any(not isinstance(row, str) for row in uncertain):
        raise ValueError("图片识别必须返回 uncertain_regions 数组。")
    if output["status"] == "recognized" and not output["text"].strip():
        raise ValueError("图片识别声称已识别文字，但未返回文字。")
    if output["status"] == "no_text" and (output["text"].strip() or not output["visual_description"].strip()):
        raise ValueError("无文字图片必须提供画面描述，且不能同时声称识别到文字。")
