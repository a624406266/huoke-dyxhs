from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from douyin_adapter import DouyinBrowserActionAdapter
from outreach import normalize_payload
from xhs_browser_actions import XhsBrowserActionAdapter, _PageActions, _account_from_page


class Items:
    def __init__(self, *items):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    def __getattr__(self, name):
        if len(self.items) != 1:
            raise AssertionError(f"not a unique fixture element: {name}")
        return getattr(self.items[0], name)


class Element:
    def __init__(self, text="", attrs=None, click=None):
        self.text = text
        self.attrs = attrs or {}
        self.on_click = click
        self.children = {}
        self.buttons = []
        self.clicks = 0
        self.author_links = []

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        return self.text

    def locator(self, selector):
        return self.children.get(selector, Items())

    def evaluate(self, script):
        return self.author_links

    def get_by_role(self, role, name, exact=False):
        return Items(*(button for button in self.buttons if (name.search(button.text) if isinstance(name, re.Pattern) else name == button.text)))

    def get_by_text(self, text, exact=False):
        return self.children.get("text:" + text, Items())

    def fill(self, text, timeout=0):
        self.text = text

    def click(self, timeout=0):
        self.clicks += 1
        if self.on_click:
            self.on_click()


class FixturePage(Element):
    def __init__(self):
        super().__init__()
        self.url = ""
        self.account_links = [{"href": "https://www.xiaohongshu.com/user/profile/account-a"}]
        self.reload_callback = lambda: None

    def evaluate(self, script):
        return self.account_links

    def goto(self, url, **kwargs):
        self.url = url

    def reload(self, **kwargs):
        self.reload_callback()

    def wait_for_timeout(self, value):
        pass


def action(kind="follow", **changes):
    value = {"platform": "xhs", "action": kind, "targetUserId": "target-1", "text": "你好" if kind != "follow" else ""}
    if kind in {"comment", "reply_comment"}:
        value["noteId"] = "note-1"
    value.update(changes)
    return {**normalize_payload(value), "accountIdentity": "xhs:account-a"}


class BrowserActionTests(unittest.TestCase):
    def profile_fixture(self, after_reload=True):
        page, profile = FixturePage(), Element()
        button = Element("关注")
        profile.buttons = [button]
        page.children[".user-page .user-info, .user-profile .user-info"] = Items(profile)
        if after_reload:
            page.reload_callback = lambda: setattr(profile, "buttons", [Element("已关注")])
        return page, button

    def test_follow_requires_verified_state_after_reload(self):
        page, button = self.profile_fixture()
        result = _PageActions(page, "xhs").execute(action())
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(button.clicks, 1)
        self.assertEqual(result["evidence"]["method"], "target-profile-follow-state-after-reload")

    def test_click_without_receipt_is_unknown(self):
        page, button = self.profile_fixture(after_reload=False)
        result = _PageActions(page, "xhs").execute(action())
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["submitted"])
        self.assertEqual(button.clicks, 1)

    def test_timeout_during_click_is_unknown_and_not_retried(self):
        page, button = self.profile_fixture()
        button.on_click = Mock(side_effect=TimeoutError("lost response"))
        result = _PageActions(page, "xhs").execute(action())
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(button.clicks, 1)

    def test_wrong_browser_account_stops_before_click(self):
        page, button = self.profile_fixture()
        page.account_links = [{"href": "https://www.xiaohongshu.com/user/profile/account-b"}]
        result = _PageActions(page, "xhs").execute(action())
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])
        self.assertEqual(button.clicks, 0)

    def test_account_requires_unique_stable_self_link(self):
        page = FixturePage()
        self.assertEqual(_account_from_page(page, "xhs")["identity"], "xhs:account-a")
        page.account_links = []
        with self.assertRaises(RuntimeError):
            _account_from_page(page, "xhs")
        page.account_links = [{"href": "/user/profile/a"}, {"href": "/user/profile/b"}]
        with self.assertRaises(RuntimeError):
            _account_from_page(page, "xhs")

    def comment_fixture(self, receipt=True):
        page, note, composer = FixturePage(), Element(), Element()
        page.children[".note-detail-mask"] = Items(note)
        note.children['a[href*="/user/profile/target-1"]'] = Items(Element())
        note.children['[data-comment-id], [data-cid], .comment-item[id]'] = Items()
        note.children['.comment-input, .comment-input-area, [data-e2e="comment-input"]'] = Items(composer)
        composer.children['textarea, [contenteditable="true"], input[type="text"]'] = Items(Element())
        send = Element("发送")
        composer.buttons = [send]
        row = Element(attrs={"data-comment-id": "new-comment"})
        row.author_links = ["https://www.xiaohongshu.com/user/profile/account-a"]
        row.children['a[href*="/user/profile/account-a"]'] = Items(Element())
        row.children['.content, .comment-content, [data-e2e="comment-content"]'] = Items(Element("你好"))
        if receipt:
            page.reload_callback = lambda: note.children.update({'[data-comment-id], [data-cid], .comment-item[id]': Items(row)})
        return page, send

    def test_comment_requires_new_own_comment_matching_text(self):
        page, send = self.comment_fixture()
        result = _PageActions(page, "xhs").execute(action("comment"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["evidence"]["resultId"], "new-comment")
        self.assertEqual(send.clicks, 1)

    def test_comment_without_post_reload_receipt_is_unknown(self):
        page, send = self.comment_fixture(receipt=False)
        self.assertEqual(_PageActions(page, "xhs").execute(action("comment"))["status"], "unknown")
        self.assertEqual(send.clicks, 1)

    def test_other_author_mention_or_similar_id_cannot_prove_my_comment(self):
        for authors in (
            ["https://www.xiaohongshu.com/user/profile/attacker"],
            ["https://www.xiaohongshu.com/user/profile/account-a-extra"],
            ["https://evil.invalid/user/profile/account-a"],
            ["https://www.xiaohongshu.com/user/profile/account-a", "https://www.xiaohongshu.com/user/profile/attacker"],
            [],
        ):
            with self.subTest(authors=authors):
                page, send = self.comment_fixture(receipt=False)
                note = page.children[".note-detail-mask"].nth(0)
                row = Element(attrs={"data-comment-id": "someone-elses-comment"})
                row.author_links = authors
                # The old whole-comment selector also matches mentions/nested replies.
                row.children['a[href*="/user/profile/account-a"]'] = Items(Element())
                row.children['.content, .comment-content, [data-e2e="comment-content"]'] = Items(Element("你好"))
                page.reload_callback = lambda: note.children.update({'[data-comment-id], [data-cid], .comment-item[id]': Items(row)})
                result = _PageActions(page, "xhs").execute(action("comment"))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["evidence"], {})
                self.assertEqual(send.clicks, 1)

    def test_reply_cannot_guess_target_comment(self):
        page, send = self.comment_fixture()
        result = _PageActions(page, "xhs").execute(action("reply_comment", commentId="missing"))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])
        self.assertEqual(send.clicks, 0)

    def reply_fixture(self, author="target-1"):
        page, send = self.comment_fixture()
        note = page.children[".note-detail-mask"].nth(0)
        composer = note.children['.comment-input, .comment-input-area, [data-e2e="comment-input"]'].nth(0)
        target = Element(attrs={"data-comment-id": "original-comment"})
        target.author_links = [f"https://www.xiaohongshu.com/user/profile/{author}"]
        target.children['a[href*="/user/profile/target-1"]'] = Items(Element())
        target.children["text:回复"] = Items(Element("回复"))
        note.children['[data-comment-id="original-comment"], [data-cid="original-comment"], [id="comment-original-comment"]'] = Items(target)
        note.children['[data-reply-to="original-comment"], [data-reply-comment-id="original-comment"]'] = Items(composer)
        note.children['[data-comment-id], [data-cid], .comment-item[id]'] = Items(target)
        row = Element(attrs={"data-comment-id": "new-reply", "data-parent-id": "original-comment"})
        row.author_links = ["https://www.xiaohongshu.com/user/profile/account-a"]
        row.children['a[href*="/user/profile/account-a"]'] = Items(Element())
        row.children['.content, .comment-content, [data-e2e="comment-content"]'] = Items(Element("你好"))
        page.reload_callback = lambda: note.children.update({'[data-comment-id], [data-cid], .comment-item[id]': Items(target, row)})
        return page, send

    def test_reply_verifies_parent_comment_and_new_own_result(self):
        page, send = self.reply_fixture()
        result = _PageActions(page, "xhs").execute(action("reply_comment", commentId="original-comment"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["evidence"]["resultId"], "new-reply")
        self.assertEqual(send.clicks, 1)

    def test_reply_rejects_another_author_even_if_target_is_mentioned(self):
        page, send = self.reply_fixture(author="another-author")
        result = _PageActions(page, "xhs").execute(action("reply_comment", commentId="original-comment"))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])
        self.assertEqual(send.clicks, 0)

    def test_dm_requires_explicit_target_conversation(self):
        page, profile = FixturePage(), Element()
        entry = Element("私信")
        profile.buttons = [entry]
        page.children[".user-page .user-info, .user-profile .user-info"] = Items(profile)
        result = _PageActions(page, "xhs").execute(action("send_dm"))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])
        self.assertEqual(entry.clicks, 1)

    def test_dm_success_requires_new_acknowledged_row_in_target_conversation(self):
        page, profile, conversation = FixturePage(), Element(), Element()
        profile.buttons = [Element("私信")]
        page.children[".user-page .user-info, .user-profile .user-info"] = Items(profile)
        page.children['[data-peer-id="target-1"], [data-conversation-user-id="target-1"]'] = Items(conversation)
        conversation.children['textarea, [contenteditable="true"], [role="textbox"]'] = Items(Element())
        conversation.children['[data-message-id]'] = Items(Element("旧消息", {"data-message-id": "old-message"}))
        row_selector = '[data-message-id][data-status="sent"][data-direction="outgoing"], [data-message-id][data-status="delivered"][data-direction="outgoing"]'
        send = Element("发送", click=lambda: conversation.children.update({row_selector: Items(Element("你好", {"data-message-id": "new-message"}))}))
        conversation.buttons = [send]
        result = _PageActions(page, "xhs").execute(action("send_dm"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["evidence"]["resultId"], "new-message")
        self.assertEqual(send.clicks, 1)

    def test_preexisting_message_does_not_prove_new_send(self):
        page, profile, conversation = FixturePage(), Element(), Element()
        profile.buttons = [Element("私信")]
        page.children[".user-page .user-info, .user-profile .user-info"] = Items(profile)
        page.children['[data-peer-id="target-1"], [data-conversation-user-id="target-1"]'] = Items(conversation)
        conversation.children['textarea, [contenteditable="true"], [role="textbox"]'] = Items(Element())
        old = Element("你好", {"data-message-id": "old-message"})
        conversation.children['[data-message-id]'] = Items(old)
        conversation.children['[data-message-id][data-status="sent"][data-direction="outgoing"], [data-message-id][data-status="delivered"][data-direction="outgoing"]'] = Items(old)
        send = Element("发送")
        conversation.buttons = [send]
        result = _PageActions(page, "xhs").execute(action("send_dm"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(send.clicks, 1)

    def test_missing_managed_browser_is_pre_submit_failure(self):
        adapter = XhsBrowserActionAdapter(SimpleNamespace(port=None))
        result = adapter.execute(action())
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["submitted"])

    def test_subprocess_timeout_is_not_claimed_as_failure_before_submit(self):
        helper = SimpleNamespace(port=9230, root=SCRIPTS.parent, _python=lambda: Path(sys.executable))
        adapter = XhsBrowserActionAdapter(helper)
        with patch("xhs_browser_actions.subprocess.run", side_effect=subprocess.TimeoutExpired("browser", 80)):
            result = adapter.execute(action())
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["submitted"])

    def test_douyin_uses_same_verified_adapter_contract(self):
        adapter = DouyinBrowserActionAdapter(SimpleNamespace())
        adapter._browser = Mock()
        adapter._browser.account_identity.return_value = {"identity": "douyin:account-a"}
        adapter._browser.execute.return_value = {"status": "unknown", "submitted": True, "evidence": {}, "error": "未获得回执"}
        self.assertEqual(adapter.account_identity()["identity"], "douyin:account-a")
        payload = {**action("send_dm"), "platform": "douyin"}
        result = adapter.execute(payload)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["submitted"])
        adapter._browser.execute.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
