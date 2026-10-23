"""Startup is exclusive and may recover data only after a successful bind."""
from __future__ import annotations

import copy
import importlib.util
import os
import socket
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from config_loader import DEFAULT_CONFIG
from instance_lock import DatabaseInstanceLock


def load_server():
    spec = importlib.util.spec_from_file_location("lifecycle_server", SCRIPTS / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServerLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config["storage"]["db_path"] = str(self.root / "test.db")
        self.config["server"] = {"host": "127.0.0.1", "port": 0}
        self.config["auth"]["admin_password_hash"] = ""
        env = patch.dict(os.environ, {"XHS_ADMIN_PASSWORD": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_import_does_not_load_local_configuration_or_open_database(self):
        with patch("config_loader.load_config", side_effect=AssertionError("must not read config")), \
                patch("storage.SalesStorage", side_effect=AssertionError("must not open database")):
            server = load_server()
        self.assertIsNone(server.STORAGE)
        self.assertIsNone(server.WORKBENCH)
        self.assertEqual(server.CONFIG, {})
        self.assertFalse(list(self.root.iterdir()))

    def test_occupied_port_does_not_initialize_or_recover_data(self):
        server = load_server()
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            self.config["server"]["port"] = occupied.getsockname()[1]
            with patch.object(server, "initialize_app", side_effect=AssertionError("must not initialize")) as initialize:
                with self.assertRaises(OSError):
                    server.WorkbenchHTTPServer(self.config)
                initialize.assert_not_called()
        self.assertFalse(list(self.root.iterdir()))

    def test_second_port_cannot_recover_running_task_in_owned_database(self):
        server = load_server()
        first = server.WorkbenchHTTPServer(self.config)
        self.addCleanup(first.server_close)
        task_id = server.STORAGE.create_task({"keyword": "fixture"}, sales_user_id=0)
        server.STORAGE.update_task(task_id, status="running")
        before = server.STORAGE.get_task(task_id)
        with self.assertRaisesRegex(RuntimeError, "已有工作台"):
            server.WorkbenchHTTPServer(self.config)
        self.assertEqual(server.STORAGE.get_task(task_id), before)
        first.server_close()
        reopened = server.WorkbenchHTTPServer(self.config)
        self.addCleanup(reopened.server_close)
        self.assertNotEqual(server.STORAGE.get_task(task_id)["status"], "running")

    def test_database_alias_uses_same_lock_and_lock_releases_on_close(self):
        original = self.root / "real.db"
        original.touch()
        alias = self.root / "alias.db"
        alias.symlink_to(original)
        first = DatabaseInstanceLock(original)
        second = DatabaseInstanceLock(alias)
        self.assertEqual(first.path, second.path)
        first.acquire()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        with self.assertRaises(RuntimeError):
            second.acquire()
        first.close()
        second.acquire()

    def test_initialization_failure_releases_database_lock(self):
        server = load_server()
        with patch.object(server, "initialize_app", side_effect=ValueError("invalid fixture")):
            with self.assertRaisesRegex(ValueError, "invalid fixture"):
                server.WorkbenchHTTPServer(self.config)
        guard = DatabaseInstanceLock(Path(self.config["storage"]["db_path"]))
        self.addCleanup(guard.close)
        guard.acquire()

    def test_simultaneous_login_requests_share_one_helper_per_platform(self):
        server = load_server()
        for factory, constructor in ((server.get_login_helper, "XhsLoginHelper"),
                                     (server.get_douyin_login_helper, "DouyinLoginHelper")):
            with self.subTest(platform=constructor):
                entered = threading.Event()
                release = threading.Event()
                second_started = threading.Event()
                helper = object()
                def construct(*args, **kwargs):
                    entered.set()
                    self.assertTrue(release.wait(2))
                    return helper
                def second_request():
                    second_started.set()
                    return factory(0)
                with patch.object(server, constructor, side_effect=construct) as create, ThreadPoolExecutor(2) as pool:
                    first = pool.submit(factory, 0)
                    self.assertTrue(entered.wait(2))
                    second = pool.submit(second_request)
                    self.assertTrue(second_started.wait(2))
                    release.set()
                    self.assertIs(first.result(), helper)
                    self.assertIs(second.result(), helper)
                    create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
