"""Exercise bootstrap security ordering without installing any packages."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap_deps as bootstrap


class BootstrapSecurityTests(unittest.TestCase):
    def test_old_pip_uses_hash_pinned_wheel_without_index_or_dependencies(self):
        with patch.object(bootstrap, "has_secure_pip", side_effect=[False, True]), \
                patch.object(bootstrap, "run") as run:
            bootstrap.ensure_secure_pip()
        args = run.call_args.args[0]
        self.assertIn("--no-index", args)
        self.assertIn("--no-deps", args)
        self.assertIn("--disable-pip-version-check", args)
        self.assertIn("--isolated", args)
        self.assertIn("#sha256=", args[-1])
        self.assertEqual(len(args[-1].split("#sha256=")[1]), hashlib.sha256().digest_size * 2)
        self.assertEqual(args[-1], bootstrap.SECURE_PIP_REQUIREMENT)

    def test_current_pip_does_not_download_or_downgrade(self):
        with patch.object(bootstrap, "has_secure_pip", return_value=True), \
                patch.object(bootstrap, "run") as run:
            bootstrap.ensure_secure_pip()
        run.assert_not_called()

    def test_failed_pip_upgrade_stops_before_business_dependencies(self):
        with patch.object(bootstrap, "create_venv"), \
                patch.object(bootstrap, "has_secure_pip", return_value=False), \
                patch.object(bootstrap, "cleanup_pip_temp_dirs") as cleanup, \
                patch.object(bootstrap, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "安全版本校验失败"):
                bootstrap.install_deps()
        self.assertEqual(run.call_count, 1)
        cleanup.assert_not_called()

    def test_cached_requirements_do_not_skip_pip_security_check(self):
        with tempfile.TemporaryDirectory() as folder:
            stamp = Path(folder) / "stamp"
            stamp.write_text("same\n")
            steps = []
            with patch.object(bootstrap, "STAMP", stamp), \
                    patch.object(bootstrap, "requirements_hash", return_value="same"), \
                    patch.object(bootstrap, "create_venv"), \
                    patch.object(bootstrap, "ensure_secure_pip", side_effect=lambda: steps.append("pip")), \
                    patch.object(bootstrap, "cleanup_pip_temp_dirs"), \
                    patch.object(bootstrap, "verify_imports", side_effect=lambda: steps.append("imports")), \
                    patch.object(bootstrap, "run") as run:
                bootstrap.install_deps()
            self.assertEqual(steps, ["pip", "imports"])
            run.assert_not_called()

    def test_failed_verification_does_not_mark_requirements_as_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            stamp = Path(folder) / "stamp"
            with patch.object(bootstrap, "STAMP", stamp), \
                    patch.object(bootstrap, "requirements_hash", return_value="new"), \
                    patch.object(bootstrap, "create_venv"), \
                    patch.object(bootstrap, "ensure_secure_pip"), \
                    patch.object(bootstrap, "cleanup_pip_temp_dirs"), \
                    patch.object(bootstrap, "run"), \
                    patch.object(bootstrap, "verify_imports", side_effect=RuntimeError("broken dependency")):
                with self.assertRaisesRegex(RuntimeError, "broken dependency"):
                    bootstrap.install_deps()
            self.assertFalse(stamp.exists())


if __name__ == "__main__":
    unittest.main()
