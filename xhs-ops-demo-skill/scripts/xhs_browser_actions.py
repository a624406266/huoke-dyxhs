"""Conservative, single-action UI adapter for a managed browser session.

No browser is started and no alternate profile is selected here.  Missing or
ambiguous account/target/receipt information stops the operation.  The module's
CLI is an internal subprocess bridge so Playwright can use the login helper's
existing Python environment.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from outreach import normalize_identity, normalize_payload
from xhs_login_helper import xhs_python


_COMMENT_AUTHOR_LINKS = """row => {
    const rows = '[data-comment-id], [data-cid], .comment-item[id]';
    const authors = '[data-comment-author], [data-e2e="comment-author"], [data-e2e="comment-user-name"], .author-wrapper, .author, .user-info';
    const content = '.content, .comment-content, [data-e2e="comment-content"]';
    return [...row.querySelectorAll('a[href]')].filter(link => {
        // A mention or a nested reply is not this comment's author.
        if (link.closest(rows) !== row) return false;
        const body = link.closest(content);
        if (body && row.contains(body)) return false;
        const author = link.closest(authors);
        return Boolean(author && row.contains(author));
    }).map(link => link.href);
}"""


class XhsBrowserActionAdapter:
    platform = "xhs"

    def __init__(self, helper: Any, *, platform: str = "xhs") -> None:
        if platform not in {"xhs", "douyin"}:
            raise ValueError("不支持的平台。")
        self.helper = helper
        self.platform = platform

    def account_identity(self) -> dict[str, str]:
        result = self._run_browser({"operation": "identity"})
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "未能核验当前受管浏览器的登录账号。")
        return normalize_identity(result, self.platform)

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = normalize_payload(action)
            if payload["platform"] != self.platform:
                raise ValueError("平台不一致。")
            identity = normalize_identity(action.get("accountIdentity"), self.platform)["identity"]
        except (ValueError, TypeError) as exc:
            return {"status": "failed", "evidence": {}, "submitted": False, "error": str(exc)}
        return self._run_browser({"operation": "execute", "action": {**payload, "accountIdentity": identity}})

    def _run_browser(self, request: dict[str, Any]) -> dict[str, Any]:
        port = getattr(self.helper, "port", None)
        if not port:
            return {"ok": False, "status": "failed", "submitted": False, "evidence": {},
                    "error": "请先打开工作台管理的网页登录窗口并完成登录；二维码登录本身不足以进行网页触达。"}
        if hasattr(self.helper, "_python"):
            python = self.helper._python()
        else:
            root = Path(self.helper.root)
            candidate = root / ".runtime" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python = candidate if candidate.exists() else xhs_python()
        try:
            proc = subprocess.run(
                [str(python), str(Path(__file__).resolve()), self.platform, str(int(port))],
                input=json.dumps(request, ensure_ascii=False), text=True, capture_output=True,
                timeout=80, cwd=str(self.helper.root), encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
        except OSError:
            return {"ok": False, "status": "failed", "submitted": False, "evidence": {}, "error": "浏览器执行环境不可用。"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "status": "unknown", "submitted": True, "evidence": {},
                    "error": "浏览器执行超时，结果未知；请人工核对，禁止自动重发。"}
        try:
            data = json.loads(proc.stdout)
            if not isinstance(data, dict):
                raise ValueError("result is not an object")
            return data
        except (ValueError, TypeError):
            return {"ok": False, "status": "unknown", "submitted": True, "evidence": {},
                    "error": "浏览器未返回完整执行记录，结果未知；请人工核对。"}


def _profile_id(href: str, platform: str) -> str:
    parsed = urlparse(href)
    hosts = {"www.xiaohongshu.com", "xiaohongshu.com"} if platform == "xhs" else {"www.douyin.com", "douyin.com"}
    if parsed.hostname and parsed.hostname not in hosts:
        return ""
    path = parsed.path
    pattern = r"^/user/profile/([A-Za-z0-9_-]+)/?$" if platform == "xhs" else r"^/user/([A-Za-z0-9_-]+)/?$"
    match = re.match(pattern, path)
    return match.group(1) if match else ""


def _account_from_page(page: Any, platform: str) -> dict[str, str]:
    # Only the navigation's self-profile link identifies the logged-in account.
    # A profile being viewed, a nickname, or a cookie count is not identity.
    result = page.evaluate("""() => {
        const links = [...document.querySelectorAll('a[href]')].filter(a => {
            const visible = a.getClientRects().length > 0;
            const label = (a.getAttribute('aria-label') || a.innerText || '').trim();
            return visible && ['我', '我的', '个人主页', '我的主页', '我的抖音'].includes(label)
                && Boolean(a.closest('nav, aside, header, .side-bar, .sidebar, [role="navigation"]'));
        });
        return links.map(a => ({href:a.href, label:(a.innerText || '').trim()}));
    }""")
    ids = {_profile_id(item.get("href", ""), platform) for item in result if isinstance(item, dict)}
    ids.discard("")
    if len(ids) != 1:
        raise RuntimeError("未找到唯一的当前账号个人主页链接，请人工确认登录账号后重试。")
    user_id = next(iter(ids))
    return {"identity": f"{platform}:{user_id}", "userId": user_id, "name": ""}


def _unique(locator: Any, label: str) -> Any:
    visible = []
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            visible.append(item)
    if len(visible) != 1:
        raise RuntimeError(f"无法唯一定位{label}，本次未提交。")
    return visible[0]


class _PageActions:
    def __init__(self, page: Any, platform: str) -> None:
        self.page = page
        self.platform = platform
        self.submitted = False

    def _account_matches(self, action: dict[str, Any]) -> None:
        if _account_from_page(self.page, self.platform)["identity"] != action["accountIdentity"]:
            raise RuntimeError("浏览器当前账号与已确认账号不一致，本次未提交。")

    def _risk_check(self) -> None:
        for text in ("安全验证", "请完成验证", "请拖动滑块", "操作频繁", "发送失败", "账号异常"):
            if self.page.get_by_text(text, exact=True).count():
                raise RuntimeError("页面要求验证或提示操作异常，请人工处理。")

    def _target_matches(self, action: dict[str, Any]) -> None:
        expected = urlparse(action["targetUrl"])
        actual = urlparse(self.page.url)
        if actual.hostname != expected.hostname or actual.path.rstrip("/") != expected.path.rstrip("/"):
            raise RuntimeError("页面跳转后的目标与确认目标不同，本次未提交。")

    def _profile_scope(self) -> Any:
        selector = ".user-page .user-info, .user-profile .user-info" if self.platform == "xhs" else '[data-e2e="user-info"]'
        return _unique(self.page.locator(selector), "目标用户资料区")

    def _note_scope(self, action: dict[str, Any]) -> Any:
        selector = ".note-detail-mask" if self.platform == "xhs" else '[data-e2e="video-detail"]'
        scope = _unique(self.page.locator(selector), "目标笔记/作品详情")
        if action.get("targetUserId") and action["action"] == "comment":
            profile = (f"/user/profile/{action['targetUserId']}" if self.platform == "xhs" else f"/user/{action['targetUserId']}")
            author_links = scope.locator(f'a[href*="{profile}"]')
            if not author_links.count():
                raise RuntimeError("无法在目标作品核验指定用户，本次未提交。")
        return scope

    def _receipt(self, action: dict[str, Any], method: str, **extra: Any) -> dict[str, Any]:
        return {"verified": True, "method": method, "observedAt": time.time(), "url": self.page.url,
                "accountIdentity": action["accountIdentity"], "targetUserId": action["targetUserId"],
                "noteId": action["noteId"], "commentId": action["commentId"], "text": action["text"], **extra}

    def _submit(self, button: Any, action: dict[str, Any]) -> None:
        self._risk_check()
        self._account_matches(action)
        self._target_matches(action)
        if not button.is_enabled():
            raise RuntimeError("提交按钮不可用，本次未提交。")
        # Set BEFORE click: a timeout may happen after the website received it.
        self.submitted = True
        button.click(timeout=5000)

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            self.page.goto(action["targetUrl"], wait_until="domcontentloaded", timeout=25000)
            self.page.wait_for_timeout(1200)
            self._target_matches(action)
            self._account_matches(action)
            self._risk_check()
            if action["action"] == "follow":
                evidence = self._follow(action)
            elif action["action"] in {"comment", "reply_comment"}:
                evidence = self._comment(action)
            else:
                evidence = self._dm(action)
            return {"ok": True, "status": "succeeded", "submitted": self.submitted, "evidence": evidence, "error": ""}
        except Exception as exc:
            return {"ok": False, "status": "unknown" if self.submitted else "failed",
                    "submitted": self.submitted, "evidence": {}, "error": str(exc)[:1000]}

    def _follow(self, action: dict[str, Any]) -> dict[str, Any]:
        scope = self._profile_scope()
        followed = scope.get_by_role("button", name=re.compile(r"^(已关注|互相关注)$"))
        if followed.count() == 1 and followed.is_visible():
            return self._receipt(action, "target-profile-follow-state", alreadyFollowed=True)
        button = _unique(scope.get_by_role("button", name=re.compile(r"^(关注|\+ ?关注)$")), "关注按钮")
        self._submit(button, action)
        # Reload to avoid mistaking an optimistic client-side toggle for success.
        self.page.reload(wait_until="domcontentloaded", timeout=20000)
        self.page.wait_for_timeout(1000)
        self._account_matches(action)
        self._target_matches(action)
        followed = self._profile_scope().get_by_role("button", name=re.compile(r"^(已关注|互相关注)$"))
        _unique(followed, "刷新后的已关注状态")
        return self._receipt(action, "target-profile-follow-state-after-reload", alreadyFollowed=False)

    def _comment_rows(self, scope: Any) -> Any:
        return scope.locator('[data-comment-id], [data-cid], .comment-item[id]')

    @staticmethod
    def _row_id(row: Any) -> str:
        return str(row.get_attribute("data-comment-id") or row.get_attribute("data-cid") or row.get_attribute("id") or "")

    def _comment_author_id(self, row: Any) -> str:
        links = row.evaluate(_COMMENT_AUTHOR_LINKS)
        if not isinstance(links, list) or not links:
            return ""
        identities = {_profile_id(link, self.platform) for link in links if isinstance(link, str)}
        # Invalid or conflicting author links fail closed; never infer identity
        # from a nickname, a mention, or a matching prefix in another profile ID.
        return next(iter(identities)) if len(identities) == 1 and "" not in identities else ""

    def _comment(self, action: dict[str, Any]) -> dict[str, Any]:
        scope = self._note_scope(action)
        rows = self._comment_rows(scope)
        before = {self._row_id(rows.nth(index)) for index in range(rows.count())}
        if action["action"] == "reply_comment":
            comment_id = action["commentId"]
            target = _unique(scope.locator(f'[data-comment-id="{comment_id}"], [data-cid="{comment_id}"], [id="comment-{comment_id}"]'), "待回复评论")
            if self._comment_author_id(target) != action["targetUserId"]:
                raise RuntimeError("待回复评论的作者与目标用户不一致。")
            reply = _unique(target.get_by_text("回复", exact=True), "评论回复入口")
            reply.click(timeout=5000)
            # The composer must expose its reply target, otherwise no safe send.
            composer = _unique(scope.locator(f'[data-reply-to="{comment_id}"], [data-reply-comment-id="{comment_id}"]'), "绑定原评论的回复输入区")
        else:
            composer = _unique(scope.locator('.comment-input, .comment-input-area, [data-e2e="comment-input"]'), "评论输入区")
        editor = _unique(composer.locator('textarea, [contenteditable="true"], input[type="text"]'), "评论输入框")
        editor.fill(action["text"], timeout=5000)
        button = _unique(composer.get_by_role("button", name=re.compile(r"^(发送|发布|评论|回复)$")), "评论提交按钮")
        self._submit(button, action)
        self.page.reload(wait_until="domcontentloaded", timeout=20000)
        self.page.wait_for_timeout(1200)
        self._account_matches(action)
        self._target_matches(action)
        self._risk_check()
        scope = self._note_scope(action)
        rows = self._comment_rows(scope)
        self_id = action["accountIdentity"].split(":", 1)[1]
        matched = []
        for index in range(rows.count()):
            row = rows.nth(index)
            row_id = self._row_id(row)
            if not row_id or row_id in before or self._comment_author_id(row) != self_id:
                continue
            contents = row.locator('.content, .comment-content, [data-e2e="comment-content"]')
            if contents.count() != 1 or contents.inner_text().strip() != action["text"].strip():
                continue
            if action["action"] == "reply_comment" and str(row.get_attribute("data-parent-id") or row.get_attribute("data-reply-to") or "") != action["commentId"]:
                continue
            matched.append(row_id)
        if len(matched) != 1:
            raise RuntimeError("评论已尝试提交，但未找到唯一的新评论回执；请人工核对，禁止自动重发。")
        return self._receipt(action, "new-own-comment-after-reload", resultId=matched[0])

    def _dm(self, action: dict[str, Any]) -> dict[str, Any]:
        scope = self._profile_scope()
        entry = _unique(scope.get_by_role("button", name=re.compile(r"^(私信|发私信)$")), "目标用户私信入口")
        entry.click(timeout=5000)
        self.page.wait_for_timeout(800)
        target = action["targetUserId"]
        conversation = _unique(self.page.locator(f'[data-peer-id="{target}"], [data-conversation-user-id="{target}"]'), "目标用户会话")
        rows = conversation.locator('[data-message-id]')
        before = {rows.nth(index).get_attribute("data-message-id") for index in range(rows.count())}
        editor = _unique(conversation.locator('textarea, [contenteditable="true"], [role="textbox"]'), "私信输入框")
        editor.fill(action["text"], timeout=5000)
        button = _unique(conversation.get_by_role("button", name="发送", exact=True), "私信发送按钮")
        self._submit(button, action)
        self.page.wait_for_timeout(1500)
        self._risk_check()
        self._account_matches(action)
        # Require an acknowledged outgoing row, not a draft or optimistic bubble.
        rows = conversation.locator('[data-message-id][data-status="sent"][data-direction="outgoing"], [data-message-id][data-status="delivered"][data-direction="outgoing"]')
        matches = []
        for index in range(rows.count()):
            row = rows.nth(index)
            row_id = row.get_attribute("data-message-id")
            if row_id and row_id not in before and row.inner_text().strip() == action["text"].strip():
                matches.append(row_id)
        if len(matches) != 1:
            raise RuntimeError("私信已尝试提交，但未取得目标会话的已发送回执；请人工核对，禁止自动重发。")
        return self._receipt(action, "target-conversation-new-sent-message", resultId=matches[0])


def _browser_command(platform: str, port: int, request: dict[str, Any]) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "status": "failed", "submitted": False, "evidence": {}, "error": "当前登录助手环境缺少 Playwright，尚未提交。"}
    page = None
    driver = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10000)
            if len(browser.contexts) != 1:
                raise RuntimeError("无法唯一确定受管浏览器上下文。")
            context = browser.contexts[0]
            # Own a separate tab, never reuse or mutate a user's draft editor.
            page = context.new_page()
            try:
                if request.get("operation") == "identity":
                    url = "https://www.xiaohongshu.com/explore" if platform == "xhs" else "https://www.douyin.com/"
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(1200)
                    return {"ok": True, **_account_from_page(page, platform)}
                action = request.get("action") or {}
                payload = normalize_payload(action)
                if payload["platform"] != platform:
                    raise RuntimeError("平台不一致。")
                payload["accountIdentity"] = normalize_identity(action.get("accountIdentity"), platform)["identity"]
                driver = _PageActions(page, platform)
                return driver.execute(payload)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    except Exception as exc:
        # All writes are inside _PageActions.execute, which records their phase.
        submitted = bool(driver and driver.submitted)
        return {"ok": False, "status": "unknown" if submitted else "failed", "submitted": submitted,
                "evidence": {}, "error": str(exc)[:1000]}


if __name__ == "__main__":
    try:
        result = _browser_command(sys.argv[1], int(sys.argv[2]), json.load(sys.stdin))
    except Exception:
        result = {"ok": False, "status": "failed", "submitted": False, "evidence": {}, "error": "浏览器执行参数无效。"}
    print(json.dumps(result, ensure_ascii=False))
