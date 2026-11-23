"""Exercise packaged launch behavior against a loopback instance of another release."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import app
from relay import config


class ExistingInstance(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"product": "relay-chat", "version": self.server.relay_version,
            "settings": {"language": "zh-CN", "auto_wait_rate_limits": True}, "desktop_window": 42}).encode())

    def log_message(self, *args):
        pass


class InstanceHandoff(unittest.TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory())) / "new-data"
        self.enterContext(patch.object(os, "environ", {
            **os.environ, "RELAY_DATA_DIR": str(self.data), "CODEX_HOME": str(self.data / "codex"),
        }))
        self.old = ThreadingHTTPServer(("127.0.0.1", 0), ExistingInstance)
        self.old.relay_version = "1.1.0"
        self.old.requests = []
        self.thread = threading.Thread(target=self.old.serve_forever, kwargs={"poll_interval": .02})
        self.thread.start()
        self.addCleanup(self.close_old)
        self.desktop = Mock()

    def close_old(self):
        if self.thread.is_alive():
            self.old.shutdown()
            self.thread.join()
            self.old.server_close()

    def test_launch_after_the_user_finishes_work_in_the_old_instance(self):
        def wait(text, cancel, still_running):
            self.assertIn(config.APP_VERSION, text)
            self.assertEqual(cancel, "取消")
            self.assertTrue(still_running())
            self.assertFalse(self.data.exists())
            self.close_old()
            self.assertFalse(still_running())
            return True
        self.desktop.wait_for_exit.side_effect = wait
        server, code = app.start_or_reuse(self.old.server_port, self.desktop, False)
        self.addCleanup(server.server_close)
        self.assertEqual((code, server.server_port), (0, self.old.server_port))
        self.assertTrue((self.data / "relay.sqlite3").exists())
        self.assertEqual(set(self.old.requests), {("GET", "/api/status")})
        self.desktop.focus_window.assert_not_called()

    def test_cancel_leaves_the_existing_instance_and_store_alone(self):
        self.desktop.wait_for_exit.return_value = False
        self.assertEqual(app.start_or_reuse(self.old.server_port, self.desktop, False), (None, 0))
        self.assertTrue(self.thread.is_alive())
        self.assertFalse(self.data.exists())
        self.assertEqual(self.old.requests, [("GET", "/api/status")])
        self.desktop.focus_window.assert_not_called()

    def test_identical_or_newer_process_reuses_its_window(self):
        for version in (config.APP_VERSION, "1.10.0"):
            with self.subTest(version=version):
                self.old.relay_version = version
                self.assertEqual(app.start_or_reuse(self.old.server_port, self.desktop, False), (None, 0))
                self.desktop.focus_window.assert_called_with(42)
        self.desktop.wait_for_exit.assert_not_called()
