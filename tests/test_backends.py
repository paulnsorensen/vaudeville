from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

from conftest import MockBackend


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestClientSocket:
    def test_classify_over_real_socket(self) -> None:
        import json as _json

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            sock_path = f.name
        os.unlink(sock_path)

        response = {"verdict": "clean", "reason": "test ok", "action": "block"}
        server_done = threading.Event()

        def _serve() -> None:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            srv.listen(1)
            srv.settimeout(3.0)
            conn, _ = srv.accept()
            data = b""
            while b"\n" not in data:
                data += conn.recv(4096)
            conn.sendall((_json.dumps(response) + "\n").encode())
            conn.close()
            srv.close()
            server_done.set()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        time.sleep(0.05)

        from vaudeville.core.client import VaudevilleClient

        client = VaudevilleClient()
        client._socket_path = sock_path
        result = client.classify("test-rule", {"text": "hello"})

        server_done.wait(timeout=3.0)
        assert result is not None
        assert result.verdict == "clean"
        assert result.reason == "test ok"


class TestDaemonExtras:
    def test_cleanup_tolerates_missing_files(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        daemon = VaudevilleDaemon(
            socket_path="/tmp/nonexistent-vd.sock",
            pid_file="/tmp/nonexistent-vd.pid",
            plugin_root=PROJECT_ROOT,
            backend=MockBackend(),
        )
        daemon._cleanup()
        assert not os.path.exists("/tmp/nonexistent-vd.sock")
        assert not os.path.exists("/tmp/nonexistent-vd.pid")

    def test_cleanup_removes_existing_files(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            sock_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", dir="/tmp", delete=False) as f:
            pid_path = f.name

        daemon = VaudevilleDaemon(
            socket_path=sock_path,
            pid_file=pid_path,
            plugin_root=PROJECT_ROOT,
            backend=MockBackend(),
        )
        daemon._cleanup()
        assert not os.path.exists(sock_path)
        assert not os.path.exists(pid_path)

    def test_find_project_root_in_daemon(self) -> None:
        from vaudeville.server.daemon import _find_project_root

        result = _find_project_root()
        assert result is not None
        assert os.path.isdir(result)

    def test_find_project_root_oserror(self) -> None:
        from vaudeville.server.daemon import _find_project_root

        with patch("vaudeville.server.daemon.subprocess.run", side_effect=OSError):
            assert _find_project_root() is None

    def test_find_project_root_timeout(self) -> None:
        import subprocess

        from vaudeville.server.daemon import _find_project_root

        with patch(
            "vaudeville.server.daemon.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            assert _find_project_root() is None

    def test_accept_loop_stops_on_stop_event(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            sock_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", dir="/tmp", delete=False) as f:
            pid_path = f.name
        os.unlink(sock_path)

        daemon = VaudevilleDaemon(sock_path, pid_path, PROJECT_ROOT, MockBackend())
        mock_server = MagicMock()
        mock_server.accept.side_effect = socket.timeout
        daemon._stop_event.set()
        daemon._accept_loop(mock_server)

    def test_handle_client_exception_logged(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        daemon = VaudevilleDaemon(
            "/tmp/x.sock", "/tmp/x.pid", PROJECT_ROOT, MockBackend()
        )
        broken_conn = MagicMock()
        broken_conn.recv.side_effect = OSError("connection reset")
        daemon._handle_client(broken_conn)
        broken_conn.close.assert_called_once()

    def test_scan_dir_mtime_oserror(self) -> None:
        from vaudeville.server.daemon import _scan_dir_mtime

        result = _scan_dir_mtime("/nonexistent/path")
        assert result == 0.0
