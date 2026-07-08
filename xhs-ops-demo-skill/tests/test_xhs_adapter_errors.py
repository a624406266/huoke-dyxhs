from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from xhs_adapter import XhsAdapter


class XhsAdapterErrorTests(unittest.TestCase):
    def make_adapter(self) -> XhsAdapter:
        adapter = object.__new__(XhsAdapter)
        return adapter

    def test_local_dependency_traceback_is_not_reported_as_ip_risk(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            "  File \"site-packages/httpx/_main.py\", line 313, in <module>\n"
            "    @click.command(add_help_option=False)\n"
            "AttributeError: module 'click' has no attribute 'command'"
        )
        message = self.make_adapter().normalize_error(text)
        self.assertIn("本地 xhs-cli 依赖环境异常", message)
        self.assertIn("不是小红书账号或 IP 风控问题", message)
        self.assertNotIn("小红书接口返回风控/IP异常", message)

    def test_real_ip_risk_error_still_reports_risk(self) -> None:
        message = self.make_adapter().normalize_error("API error: 当前 IP异常，请稍后重试")
        self.assertIn("小红书接口返回风控/IP异常", message)

    def test_follow_user_uses_structured_single_target_command(self) -> None:
        adapter = self.make_adapter()
        calls = []

        def fake_run(args, timeout=90):
            calls.append((args, timeout))
            return {"ok": True, "data": {"followed": True}}

        adapter.run = fake_run  # type: ignore[method-assign]
        self.assertEqual(adapter.follow_user("user-123")["ok"], True)
        self.assertEqual(calls, [(["follow", "user-123", "--json"], 60)])

    def test_follow_user_rejects_empty_target(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "缺少可关注"):
            self.make_adapter().follow_user("")


if __name__ == "__main__":
    unittest.main()
