from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from browser_session import BrowserSession
from douyin_adapter import DouyinLoginHelper
from xhs_login_helper import XhsLoginHelper


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.process = Mock()
        self.process.poll.return_value = None
        self.spawn = self.stack.enter_context(patch("browser_session.subprocess.Popen", return_value=self.process))
        self.ready = self.stack.enter_context(patch("browser_session.cdp_ready", return_value=True))
        self.stack.enter_context(patch("browser_session.chrome_executable", return_value=Path("/fixture/chrome")))
        self.stack.enter_context(patch("browser_session.find_free_port", return_value=9230))
        self.sleep = self.stack.enter_context(patch("browser_session.time.sleep"))
        self.session = BrowserSession(self.root / "profile", (9230, 9323))

    def test_construction_has_no_browser_or_filesystem_side_effect(self):
        self.assertFalse(self.session.profile_dir.exists())
        self.spawn.assert_not_called()
        self.ready.assert_not_called()

    def test_start_reuses_owned_process_and_exact_profile(self):
        self.session.start_browser("https://fixture.invalid/login")
        self.session.start_browser("https://fixture.invalid/login")
        self.spawn.assert_called_once()
        args = self.spawn.call_args.args[0]
        self.assertIn(f"--user-data-dir={self.session.profile_dir}", args)
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertEqual(args[-1], "https://fixture.invalid/login")
        self.assertEqual(self.session.port, 9230)
        self.assertIs(self.session.process, self.process)

    def test_timeout_terminates_kills_and_waits_before_clearing_state(self):
        self.ready.return_value = False
        self.process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        with self.assertRaisesRegex(RuntimeError, "端口未就绪"):
            self.session.start_browser("https://fixture.invalid/login")
        self.process.terminate.assert_called_once()
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 2)
        self.assertEqual(self.sleep.call_count, 30)
        self.assertIsNone(self.session.process)
        self.assertIsNone(self.session.port)

    def test_early_process_exit_is_reaped_without_waiting_for_timeout(self):
        self.process.poll.return_value = 7
        with self.assertRaisesRegex(RuntimeError, "已退出"):
            self.session.start_browser("https://fixture.invalid/login")
        self.process.wait.assert_called_once_with(timeout=5)
        self.process.terminate.assert_not_called()
        self.sleep.assert_not_called()
        self.assertIsNone(self.session.process)

    def test_stale_owned_process_is_stopped_before_replacement(self):
        old = Mock()
        old.poll.return_value = None
        self.session.process, self.session.port = old, 9231
        self.ready.side_effect = [False, True]
        self.session.start_browser("https://fixture.invalid/login")
        old.terminate.assert_called_once()
        old.wait.assert_called_once_with(timeout=5)
        self.assertIs(self.session.process, self.process)

    def test_concurrent_start_creates_one_process(self):
        checking, release = threading.Event(), threading.Event()

        def ready(_port):
            checking.set()
            if not release.wait(timeout=2):
                raise AssertionError("Fixture did not release CDP readiness")
            return True

        self.ready.side_effect = ready
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.session.start_browser, "https://fixture.invalid/login")
            try:
                self.assertTrue(checking.wait(timeout=2))
                second = pool.submit(self.session.start_browser, "https://fixture.invalid/login")
            finally:
                release.set()
            first.result(timeout=2)
            second.result(timeout=2)
        self.spawn.assert_called_once()

    def test_both_helpers_use_shared_start_and_reap_before_profile_removal(self):
        for helper_type in (XhsLoginHelper, DouyinLoginHelper):
            with self.subTest(helper=helper_type.__name__):
                self.spawn.reset_mock()
                process = Mock()
                process.poll.return_value = None
                self.spawn.return_value = process
                helper = helper_type(self.root, home_dir=self.root / helper_type.__name__)
                helper.start(mode="browser")
                helper.start(mode="browser")
                self.spawn.assert_called_once()
                cache = helper.profile_dir / "fixture-cache"
                cache.write_text("fixture")
                process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
                self.assertTrue(helper.reset()["ok"])
                process.kill.assert_called_once()
                self.assertEqual(process.wait.call_count, 2)
                self.assertFalse(cache.exists())
                self.assertIsNone(helper.process)
                self.assertIsNone(helper.port)
                self.assertEqual(helper.mode, "idle")

    def test_failed_shutdown_retains_handle_and_does_not_delete_profile(self):
        for helper_type in (XhsLoginHelper, DouyinLoginHelper):
            with self.subTest(helper=helper_type.__name__):
                helper = helper_type(self.root, home_dir=self.root / helper_type.__name__)
                helper.profile_dir.mkdir(parents=True)
                cache = helper.profile_dir / "fixture-cache"
                cache.write_text("fixture")
                process = Mock()
                process.poll.return_value = None
                process.wait.side_effect = subprocess.TimeoutExpired("fixture", 5)
                helper.process, helper.port = process, 9230
                with self.assertRaises(subprocess.TimeoutExpired):
                    helper.reset()
                self.assertTrue(cache.exists())
                self.assertIs(helper.process, process)
                self.assertEqual(helper.port, 9230)
                process.wait.side_effect = None
                self.assertTrue(helper.reset()["ok"])

    def test_qr_creation_failure_closes_the_new_client(self):
        for result in (RuntimeError("fixture provider timeout"), {"url": ""}):
            with self.subTest(result=type(result).__name__):
                helper = XhsLoginHelper(self.root, home_dir=self.root / "qr-profile")
                client = Mock()
                if isinstance(result, Exception):
                    client.create_qr_login.side_effect = result
                else:
                    client.create_qr_login.return_value = result
                modules = {"xhs_cli.client": SimpleNamespace(XhsClient=Mock(return_value=client)),
                           "xhs_cli.qr_login": SimpleNamespace(_apply_session_cookies=Mock(),
                               _generate_a1=lambda: "fixture-a1", _generate_webid=lambda: "fixture-webid")}
                with patch.dict(sys.modules, modules), self.assertRaises(RuntimeError):
                    helper.start(mode="qrcode")
                client.close.assert_called_once()
                self.assertIsNone(helper.qr_client)
                self.assertEqual(helper.qr_session, {})
                self.assertEqual(helper.mode, "idle")
                self.spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
