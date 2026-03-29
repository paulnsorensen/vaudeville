"""Tests for singleton daemon contract: global socket, version stamp lifecycle."""

from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from typing import Any

from vaudeville.server.daemon import VaudevilleDaemon


# ---------------------------------------------------------------------------
# client.py contract
# ---------------------------------------------------------------------------


class TestClientSocketPath:
    def test_SOCKET_PATH_constant_exists_in_module(self) -> None:
        """client.py must export SOCKET_PATH, not SOCKET_TEMPLATE."""
        import vaudeville.core.client as client_module

        assert hasattr(client_module, "SOCKET_PATH"), (
            "SOCKET_PATH constant is missing from vaudeville.core.client"
        )

    def test_SOCKET_TEMPLATE_removed_from_module(self) -> None:
        """SOCKET_TEMPLATE must be gone — it's been replaced by SOCKET_PATH."""
        import vaudeville.core.client as client_module

        assert not hasattr(client_module, "SOCKET_TEMPLATE"), (
            "SOCKET_TEMPLATE should be removed; SOCKET_PATH replaces it"
        )

    def test_SOCKET_PATH_value_is_tmp_vaudeville_sock(self) -> None:
        """Fixed path must be /tmp/vaudeville.sock."""
        from vaudeville.core.client import SOCKET_PATH

        assert SOCKET_PATH == "/tmp/vaudeville.sock", (
            f"SOCKET_PATH is {SOCKET_PATH!r}, expected '/tmp/vaudeville.sock'"
        )

    def test_SOCKET_PATH_is_a_string(self) -> None:
        from vaudeville.core.client import SOCKET_PATH

        assert isinstance(SOCKET_PATH, str)

    def test_SOCKET_PATH_has_no_format_placeholder(self) -> None:
        """Must not contain {session_id} or any other {} placeholder."""
        from vaudeville.core.client import SOCKET_PATH

        assert "{" not in SOCKET_PATH and "}" not in SOCKET_PATH, (
            f"SOCKET_PATH must be a fixed path, not a template: {SOCKET_PATH!r}"
        )


class TestVaudevilleClientNoArgs:
    def test_constructor_accepts_no_arguments(self) -> None:
        """VaudevilleClient() must work with zero arguments."""
        from vaudeville.core.client import VaudevilleClient

        client = VaudevilleClient()
        assert client is not None

    def test_constructor_rejects_positional_session_id(self) -> None:
        """Old VaudevilleClient(session_id) signature must no longer exist."""
        import inspect

        from vaudeville.core.client import VaudevilleClient

        sig = inspect.signature(VaudevilleClient.__init__)
        params = [
            p
            for p in sig.parameters.values()
            if p.name not in ("self",)
            and p.default is inspect.Parameter.empty
            and p.kind
            not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        ]
        assert len(params) == 0, (
            f"VaudevilleClient.__init__ has required parameters: "
            f"{[p.name for p in params]}"
        )

    def test_client_uses_fixed_socket_path_internally(self) -> None:
        """Client must connect to /tmp/vaudeville.sock, not a session path."""
        from vaudeville.core.client import SOCKET_PATH, VaudevilleClient

        client = VaudevilleClient()
        # The internal socket path should match the module-level constant
        assert client._socket_path == SOCKET_PATH

    def test_client_classify_fails_open_on_missing_socket(self) -> None:
        """Fail-open semantics must still hold with no-arg constructor."""
        from vaudeville.core.client import VaudevilleClient

        client = VaudevilleClient()
        result = client.classify("violation-detector", {"text": "test"})
        assert result is None, (
            "classify() must return None when daemon is unavailable (fail-open)"
        )


# ---------------------------------------------------------------------------
# daemon.py contract
# ---------------------------------------------------------------------------


class TestDaemonConstants:
    def test_IDLE_TIMEOUT_equals_3600(self) -> None:
        """Idle timeout must be 1 hour (3600 seconds)."""
        from vaudeville.server.daemon import IDLE_TIMEOUT

        assert IDLE_TIMEOUT == 3600, (
            f"IDLE_TIMEOUT is {IDLE_TIMEOUT}, expected 3600 (1 hour)"
        )

    def test_VERSION_FILE_constant_exists(self) -> None:
        """daemon.py must export VERSION_FILE constant."""
        import vaudeville.server.daemon as daemon_module

        assert hasattr(daemon_module, "VERSION_FILE"), (
            "VERSION_FILE constant is missing from vaudeville.server.daemon"
        )

    def test_VERSION_FILE_value_is_tmp_vaudeville_version(self) -> None:
        """VERSION_FILE must be /tmp/vaudeville.version."""
        from vaudeville.server.daemon import VERSION_FILE

        assert VERSION_FILE == "/tmp/vaudeville.version", (
            f"VERSION_FILE is {VERSION_FILE!r}, expected '/tmp/vaudeville.version'"
        )

    def test_VERSION_FILE_is_a_string(self) -> None:
        from vaudeville.server.daemon import VERSION_FILE

        assert isinstance(VERSION_FILE, str)


class TestVersionStamp:
    """Version stamp file is written during serve() and removed during cleanup."""

    def _make_daemon(
        self,
        socket_path: str,
        pid_file: str,
    ) -> VaudevilleDaemon:
        from conftest import MockBackend

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return VaudevilleDaemon(socket_path, pid_file, plugin_root, MockBackend())

    def test_version_file_written_after_pid_lock(self) -> None:
        """serve() must write VERSION_FILE once the PID lock is acquired."""
        from vaudeville.server.daemon import VERSION_FILE

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)

        # Clean slate — remove version file if leftover from a previous run
        try:
            os.unlink(VERSION_FILE)
        except FileNotFoundError:
            pass

        daemon = self._make_daemon(socket_path, pid_file)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        # Wait for socket to appear (daemon is serving)
        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.05)
        else:
            daemon._stop_event.set()
            raise RuntimeError("Daemon socket never became ready")

        try:
            assert os.path.exists(VERSION_FILE), (
                f"VERSION_FILE {VERSION_FILE!r} was not written during serve()"
            )
        finally:
            daemon._stop_event.set()
            thread.join(timeout=3)

    def test_version_file_contains_nonempty_content(self) -> None:
        """VERSION_FILE must not be empty — it should contain a git HEAD hash."""
        from vaudeville.server.daemon import VERSION_FILE

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)

        try:
            os.unlink(VERSION_FILE)
        except FileNotFoundError:
            pass

        daemon = self._make_daemon(socket_path, pid_file)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.05)

        try:
            if os.path.exists(VERSION_FILE):
                content = open(VERSION_FILE).read().strip()
                assert content != "", "VERSION_FILE exists but is empty"
        finally:
            daemon._stop_event.set()
            thread.join(timeout=3)

    def test_version_file_removed_on_cleanup(self) -> None:
        """_cleanup() must remove VERSION_FILE alongside socket and PID."""
        from vaudeville.server.daemon import VERSION_FILE

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)

        try:
            os.unlink(VERSION_FILE)
        except FileNotFoundError:
            pass

        daemon = self._make_daemon(socket_path, pid_file)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        # Wait for serving
        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.05)

        # Stop daemon and wait for cleanup
        daemon._stop_event.set()
        thread.join(timeout=5)

        assert not os.path.exists(VERSION_FILE), (
            f"VERSION_FILE {VERSION_FILE!r} was not removed during _cleanup()"
        )

    def test_cleanup_removes_version_file_even_if_missing(self) -> None:
        """_cleanup() must not raise if VERSION_FILE does not exist."""
        from vaudeville.server.daemon import VaudevilleDaemon
        from conftest import MockBackend

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            "/tmp/_test_cleanup.sock",
            "/tmp/_test_cleanup.pid",
            plugin_root,
            MockBackend(),
        )

        # Ensure file absent
        from vaudeville.server.daemon import VERSION_FILE

        try:
            os.unlink(VERSION_FILE)
        except FileNotFoundError:
            pass

        # Must not raise
        daemon._cleanup()


# ---------------------------------------------------------------------------
# runner.py contract — no session_id extraction
# ---------------------------------------------------------------------------


class TestRunnerNoSessionId:
    def test_runner_constructs_client_without_session_id(self) -> None:
        """runner.main() must call VaudevilleClient() with no positional args."""
        import io
        from unittest.mock import MagicMock, patch

        hook_input = json.dumps(
            {
                "session_id": "should-be-ignored",
                "last_assistant_message": "A" * 200,
            }
        )

        mock_client = MagicMock()
        mock_client.classify.return_value = None

        captured_calls: list[Any] = []

        def fake_constructor(*args: object, **kwargs: object) -> MagicMock:
            captured_calls.append((args, kwargs))
            return mock_client

        hooks_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"
        )

        with (
            patch("sys.stdin", io.StringIO(hook_input)),
            patch("sys.stdout", io.StringIO()),
            patch("sys.argv", ["runner.py", "violation-detector"]),
            patch(
                "vaudeville.core.client.VaudevilleClient", side_effect=fake_constructor
            ),
        ):
            if hooks_dir not in sys.path:
                sys.path.insert(0, hooks_dir)
            try:
                runner = importlib.import_module("runner")
                importlib.reload(runner)
                runner.main()
            except SystemExit:
                pass
            finally:
                if hooks_dir in sys.path:
                    sys.path.remove(hooks_dir)

        assert len(captured_calls) == 1, "VaudevilleClient should be constructed once"
        args, kwargs = captured_calls[0]
        assert args == (), (
            f"VaudevilleClient must be called with no positional args, got: {args}"
        )
        assert kwargs == {}, (
            f"VaudevilleClient must be called with no keyword args, got: {kwargs}"
        )


    def test_runner_source_does_not_extract_session_id(self) -> None:
        """runner.py must not contain session_id extraction after the singleton change."""
        hooks_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"
        )
        runner_src = open(os.path.join(hooks_dir, "runner.py")).read()

        # Old pattern: session_id = hook_input.get("session_id", ...)
        assert "session_id" not in runner_src, (
            "runner.py still references session_id — remove it for the singleton client"
        )
