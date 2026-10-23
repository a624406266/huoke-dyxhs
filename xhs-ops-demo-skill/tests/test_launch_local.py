"""Process ownership and readiness checks without starting the real app."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import launch_local


class LauncherTests(unittest.TestCase):
    def test_health_requires_workbench_identity_not_just_http_success(self):
        for body, expected in [({"ok": True, "data": {"app": "keyword-workbench"}}, True),
                               ({"ok": True, "data": {"app": "other"}}, False),
                               ({"ok": False, "data": {"app": "keyword-workbench"}}, False),
                               (None, False), ({"ok": True, "data": []}, False)]:
            with self.subTest(body=body):
                response = MagicMock()
                response.__enter__.return_value.status = 200
                response.__enter__.return_value.read.return_value = json.dumps(body).encode()
                with patch.object(launch_local, "urlopen", return_value=response):
                    self.assertEqual(launch_local.health_ok("127.0.0.1", 12345), expected)

    def test_existing_service_is_reused_without_configuration_or_installation(self):
        with patch.object(sys, "argv", ["launcher", "--no-open"]), \
                patch.object(launch_local, "health_ok", return_value=True), \
                patch.object(launch_local, "run_bootstrap") as bootstrap, \
                patch.object(launch_local, "write_local_config") as write:
            self.assertEqual(launch_local.main(), 0)
            bootstrap.assert_not_called()
            write.assert_not_called()

    def test_timeout_reaps_child_including_force_kill_when_termination_stalls(self):
        for stalled in (False, True):
            with self.subTest(stalled=stalled):
                child = Mock()
                child.poll.return_value = None
                child.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0] if stalled else [0]
                with patch.object(sys, "argv", ["launcher", "--no-open"]), \
                        patch.object(launch_local, "health_ok", return_value=False), \
                        patch.object(launch_local, "is_port_open", return_value=False), \
                        patch.object(launch_local, "run_bootstrap", return_value=Path(sys.executable)), \
                        patch.object(launch_local, "write_local_config"), \
                        patch.object(launch_local, "find_xhs_bin", return_value=""), \
                        patch.object(launch_local.subprocess, "Popen", return_value=child), \
                        patch.object(launch_local.time, "sleep"):
                    with self.assertRaisesRegex(RuntimeError, "启动超时"):
                        launch_local.main()
                child.terminate.assert_called_once()
                self.assertEqual(child.wait.call_count, 2 if stalled else 1)
                self.assertEqual(child.kill.call_count, int(stalled))


if __name__ == "__main__":
    unittest.main()
