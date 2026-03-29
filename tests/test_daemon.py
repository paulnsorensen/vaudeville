"""Tests for daemon request handling and verdict parsing."""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time

from vaudeville.server.daemon import handle_request, VaudevilleDaemon
from vaudeville.core.rules import load_rules
from conftest import MockBackend


class TestHandleRequest:
    def test_clean_verdict(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(verdict="clean", reason="all good")
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "All tests pass."},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, backend))
        assert response["verdict"] == "clean"
        assert response["reason"] == "all good"

    def test_violation_verdict(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(verdict="violation", reason="premature completion")
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "This should work."},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, backend))
        assert response["verdict"] == "violation"

    def test_unknown_rule_returns_clean(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend()
        data = (
            json.dumps(
                {
                    "rule": "nonexistent-rule",
                    "input": {"text": "test"},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, backend))
        assert response["verdict"] == "clean"

    def test_malformed_json_returns_clean(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend()
        response = json.loads(handle_request(b"not-json\n", rules, backend))
        assert response["verdict"] == "clean"

    def test_backend_receives_formatted_prompt(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(verdict="clean")
        text = "unique test string xyz"
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": text},
                }
            ).encode()
            + b"\n"
        )
        handle_request(data, rules, backend)
        assert len(backend.calls) == 1
        assert text in backend.calls[0]

    def test_response_ends_with_newline(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend()
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "test"},
                }
            ).encode()
            + b"\n"
        )
        response = handle_request(data, rules, backend)
        assert response.endswith(b"\n")


class TestDaemonSocketProtocol:
    def test_daemon_serves_request_via_socket(self) -> None:
        import tempfile

        # Unix sockets have a 104-char path limit on macOS — use /tmp directly
        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)  # daemon will re-create it
        backend = MockBackend(verdict="clean", reason="socket test")
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=True
        ) as vf:
            version_file = vf.name
        daemon = VaudevilleDaemon(
            socket_path, pid_file, plugin_root, backend, version_file=version_file
        )

        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        # Wait for daemon socket to be ready
        for _ in range(20):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.05)
        else:
            raise RuntimeError("Daemon socket not ready after 1s")

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3.0)
            sock.connect(socket_path)
            payload = (
                json.dumps(
                    {
                        "rule": "violation-detector",
                        "input": {"text": "All good."},
                    }
                ).encode()
                + b"\n"
            )
            sock.sendall(payload)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk or b"\n" in data + chunk:
                    data += chunk
                    break
                data += chunk

        response = json.loads(data.decode().strip())
        assert response["verdict"] == "clean"
        daemon._stop_event.set()


class TestBackendLockSerialization:
    def test_concurrent_requests_are_serialized(self) -> None:
        """Verify that backend.classify() calls don't overlap."""
        import tempfile

        call_log: list[tuple[float, float]] = []
        lock = threading.Lock()

        class SlowBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                start = time.monotonic()
                time.sleep(0.05)
                end = time.monotonic()
                with lock:
                    call_log.append((start, end))
                return "VERDICT: clean\nREASON: ok"

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=True
        ) as vf:
            version_file = vf.name
        daemon = VaudevilleDaemon(
            socket_path, pid_file, plugin_root, SlowBackend(), version_file=version_file
        )

        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        # Wait for ready
        for _ in range(20):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.05)

        def send_request() -> None:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(3.0)
                sock.connect(socket_path)
                payload = (
                    json.dumps(
                        {"rule": "violation-detector", "input": {"text": "test"}}
                    ).encode()
                    + b"\n"
                )
                sock.sendall(payload)
                while True:
                    chunk = sock.recv(4096)
                    if not chunk or b"\n" in chunk:
                        break

        threads = [threading.Thread(target=send_request) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        daemon._stop_event.set()

        # Verify no overlapping time windows
        assert len(call_log) == 3
        sorted_calls = sorted(call_log)
        for i in range(len(sorted_calls) - 1):
            assert sorted_calls[i][1] <= sorted_calls[i + 1][0] + 0.01, (
                f"Calls overlapped: {sorted_calls[i]} and {sorted_calls[i + 1]}"
            )


class TestSignalHandlers:
    def test_sigterm_sets_stop_event(self) -> None:
        """Verify SIGTERM triggers graceful shutdown via _stop_event."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backend = MockBackend()
        daemon = VaudevilleDaemon(socket_path, pid_file, plugin_root, backend)

        # Install handlers (normally done by serve())
        daemon._install_signal_handlers()
        assert not daemon._stop_event.is_set()

        # Send SIGTERM to ourselves
        os.kill(os.getpid(), signal.SIGTERM)
        assert daemon._stop_event.is_set()


class TestVersionStamp:
    def _make_daemon(
        self,
        socket_path: str,
        pid_file: str,
        version_file: str,
    ) -> "VaudevilleDaemon":
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return VaudevilleDaemon(
            socket_path, pid_file, plugin_root, MockBackend(), version_file=version_file
        )

    def test_version_file_written_after_serve(self) -> None:
        """serve() writes the version file once the PID lock is acquired."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as f:
            version_file = f.name
        os.unlink(socket_path)

        daemon = self._make_daemon(socket_path, pid_file, version_file)
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
        else:
            daemon._stop_event.set()
            raise RuntimeError("Daemon socket never became ready")

        try:
            assert os.path.exists(version_file), "version file not written"
            content = open(version_file).read().strip()
            assert content != "", "version file is empty"
        finally:
            daemon._stop_event.set()
            thread.join(timeout=3)

    def test_version_file_cleaned_on_shutdown(self) -> None:
        """_cleanup() removes the version file after the daemon stops."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as f:
            version_file = f.name
        os.unlink(socket_path)

        daemon = self._make_daemon(socket_path, pid_file, version_file)
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

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert not os.path.exists(version_file), "version file not removed on cleanup"
