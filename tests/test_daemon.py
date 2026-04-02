"""Tests for daemon request handling and verdict parsing."""

from __future__ import annotations

import json
import os
import platform
import signal
import socket
import threading
import time
from unittest.mock import patch

import pytest

from vaudeville.server.daemon import handle_request, VaudevilleDaemon
from vaudeville.server.__main__ import detect_backend
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


def _send_request(sock_path: str, payload: dict) -> dict:
    encoded = json.dumps(payload).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3.0)
        sock.connect(sock_path)
        sock.sendall(encoded)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk or b"\n" in data + chunk:
                data += chunk
                break
            data += chunk
    return json.loads(data.decode().strip())


class TestDaemonSocketProtocol:
    def test_daemon_serves_request_via_socket(self) -> None:
        import os
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

        response = _send_request(
            socket_path,
            {"rule": "violation-detector", "input": {"text": "All good."}},
        )
        assert response["verdict"] == "clean"
        daemon._stop_event.set()

    def test_oversized_payload_returns_fail_open(self) -> None:
        import os
        import tempfile

        from vaudeville.server.daemon import MAX_REQUEST_SIZE

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", dir="/tmp", delete=False) as f:
            pid_file = f.name
        os.unlink(socket_path)

        backend = MockBackend(verdict="violation", reason="should not reach backend")
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(socket_path, pid_file, plugin_root, backend)

        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        time.sleep(0.2)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3.0)
            sock.connect(socket_path)
            sock.sendall(b"x" * (MAX_REQUEST_SIZE + 1))
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

        response = json.loads(data.decode().strip())
        assert response["verdict"] == "clean"
        assert backend.calls == []
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


class TestConfidenceScoring:
    def test_response_includes_confidence(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(
            verdict="violation",
            reason="test",
            logprobs={"violation": -0.2, "clean": -2.5},
        )
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "test"},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, backend))
        assert "confidence" in response
        assert 0.0 <= response["confidence"] <= 1.0

    def test_threshold_downgrades_low_confidence_violation(
        self, rules_dir: str
    ) -> None:
        rules = load_rules(rules_dir)
        # Set a high threshold on the rule
        rules["violation-detector"].threshold = 0.95
        backend = MockBackend(
            verdict="violation",
            reason="low confidence",
            logprobs={"violation": -0.8, "clean": -1.0},
        )
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "borderline"},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, backend))
        # Confidence ~0.55 is below threshold 0.95 → downgraded to clean
        assert response["verdict"] == "clean"

    def test_threshold_keeps_high_confidence_violation(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        rules["violation-detector"].threshold = 0.5
        backend = MockBackend(
            verdict="violation",
            reason="high confidence",
            logprobs={"violation": -0.1, "clean": -3.0},
        )
        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "clearly bad"},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, backend))
        assert response["verdict"] == "violation"

    def test_fallback_when_backend_lacks_logprobs(self, rules_dir: str) -> None:
        """Backend without classify_with_logprobs falls back gracefully."""
        rules = load_rules(rules_dir)

        class PlainBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                return "VERDICT: clean\nREASON: ok"

        data = (
            json.dumps(
                {
                    "rule": "violation-detector",
                    "input": {"text": "test"},
                }
            ).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, rules, PlainBackend()))
        assert response["verdict"] == "clean"
        assert response["confidence"] == 1.0


class TestDetectBackend:
    def test_returns_mlx_on_apple_silicon(self) -> None:
        with (
            patch("vaudeville.server.__main__.platform") as mock_platform,
            patch.dict("sys.modules", {"mlx_lm": __import__("os")}),
        ):
            mock_platform.system.return_value = "Darwin"
            mock_platform.machine.return_value = "arm64"
            assert detect_backend() == "mlx"

    def test_returns_gguf_on_linux(self) -> None:
        with (
            patch("vaudeville.server.__main__.platform") as mock_platform,
            patch.dict("sys.modules", {"llama_cpp": __import__("os")}),
        ):
            mock_platform.system.return_value = "Linux"
            mock_platform.machine.return_value = "x86_64"
            assert detect_backend() == "gguf"

    def test_returns_gguf_when_mlx_unavailable(self) -> None:
        with (
            patch("vaudeville.server.__main__.platform") as mock_platform,
            patch.dict("sys.modules", {"mlx_lm": None, "llama_cpp": __import__("os")}),
        ):
            mock_platform.system.return_value = "Darwin"
            mock_platform.machine.return_value = "arm64"
            assert detect_backend() == "gguf"

    def test_raises_when_no_backend_available(self) -> None:
        with (
            patch("vaudeville.server.__main__.platform") as mock_platform,
            patch.dict("sys.modules", {"mlx_lm": None, "llama_cpp": None}),
        ):
            mock_platform.system.return_value = "Linux"
            mock_platform.machine.return_value = "x86_64"
            import pytest

            with pytest.raises(RuntimeError, match="No inference backend"):
                detect_backend()


class TestAcquirePidLock:
    def test_acquires_and_writes_pid(self, tmp_path: str) -> None:
        import os
        from pathlib import Path
        from vaudeville.server.daemon import acquire_pid_lock

        pid_file = str(Path(tmp_path) / "test.pid")
        fd = acquire_pid_lock(pid_file)
        assert fd is not None
        content = Path(pid_file).read_text()
        assert str(os.getpid()) in content
        os.close(fd)

    def test_second_caller_gets_none(self, tmp_path: str) -> None:
        import os
        from pathlib import Path
        from vaudeville.server.daemon import acquire_pid_lock

        pid_file = str(Path(tmp_path) / "test.pid")
        fd1 = acquire_pid_lock(pid_file)
        assert fd1 is not None
        fd2 = acquire_pid_lock(pid_file)
        assert fd2 is None
        os.close(fd1)

    def test_lock_released_after_close(self, tmp_path: str) -> None:
        import os
        from pathlib import Path
        from vaudeville.server.daemon import acquire_pid_lock

        pid_file = str(Path(tmp_path) / "test.pid")
        fd1 = acquire_pid_lock(pid_file)
        assert fd1 is not None
        os.close(fd1)
        fd2 = acquire_pid_lock(pid_file)
        assert fd2 is not None
        os.close(fd2)


@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine() != "arm64",
    reason="MLX only available on Apple Silicon",
)
class TestMLXImportSmoke:
    """Verify real mlx_lm imports resolve — catches API drift."""

    def test_mlx_backend_imports_resolve(self) -> None:
        """MLXBackend.__init__ imports must not raise ImportError."""
        from mlx_lm import stream_generate  # noqa: F401
        from mlx_lm.generate import generate_step  # noqa: F401

    def test_generate_step_signature_has_sampler(self) -> None:
        """generate_step must accept sampler= (not temp=)."""
        import inspect

        from mlx_lm.generate import generate_step

        params = inspect.signature(generate_step).parameters
        assert "sampler" in params, "generate_step lost sampler= param"
        assert "temp" not in params, "generate_step still has temp= (old API)"


@pytest.mark.skipif(
    platform.system() == "Darwin" and platform.machine() == "arm64",
    reason="GGUF tests target Linux/x86 — skipped on Apple Silicon",
)
class TestGGUFImportSmoke:
    """Verify real llama_cpp imports resolve — catches API drift."""

    def test_gguf_backend_imports_resolve(self) -> None:
        """GGUFBackend.__init__ imports must not raise ImportError."""
        try:
            from llama_cpp import Llama  # noqa: F401
        except ImportError:
            pytest.skip("llama-cpp-python not installed")

    def test_llama_has_create_chat_completion(self) -> None:
        """Llama must have create_chat_completion method."""
        try:
            from llama_cpp import Llama
        except ImportError:
            pytest.skip("llama-cpp-python not installed")
        assert hasattr(Llama, "create_chat_completion"), (
            "Llama lost create_chat_completion method"
        )
