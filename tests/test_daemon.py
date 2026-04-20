"""Tests for daemon request handling and verdict parsing."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import signal
import socket
import threading
import time
from unittest.mock import patch

import pytest

from vaudeville.core.protocol import ClassifyResult
from vaudeville.server import DaemonConfig, VaudevilleDaemon
from vaudeville.server._handlers import handle_request
from vaudeville.server.event_log import EventLogger
from vaudeville.server.log_config import LogConfig
from vaudeville.server.inference import LogprobBackend
from vaudeville.server.__main__ import detect_backend
from conftest import MockBackend  # noqa: F401 — used in fixtures


class TestHandleRequest:
    def test_clean_verdict(self) -> None:
        backend = MockBackend(verdict="clean", reason="all good")
        data = json.dumps({"prompt": "classify this text"}).encode() + b"\n"
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "clean"
        assert response["reason"] == "all good"

    def test_violation_verdict(self) -> None:
        backend = MockBackend(verdict="violation", reason="premature completion")
        data = json.dumps({"prompt": "classify this"}).encode() + b"\n"
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "violation"

    def test_malformed_json_returns_clean(self) -> None:
        backend = MockBackend()
        response = json.loads(handle_request(b"not-json\n", backend))
        assert response["verdict"] == "clean"

    def test_backend_receives_prompt(self) -> None:
        backend = MockBackend(verdict="clean")
        prompt = "unique test string xyz"
        data = json.dumps({"prompt": prompt}).encode() + b"\n"
        handle_request(data, backend)
        assert len(backend.calls) == 1
        assert prompt in backend.calls[0]

    def test_response_ends_with_newline(self) -> None:
        backend = MockBackend()
        data = json.dumps({"prompt": "test"}).encode() + b"\n"
        response = handle_request(data, backend)
        assert response.endswith(b"\n")

    def test_classify_max_tokens_is_tight(self) -> None:
        """Runtime must cap Phi at ~one short sentence to avoid hallucinated run-ons.

        Regression for bug where `max_tokens=50` let Phi-4-mini continue past
        the REASON line (e.g. "...confirmed fix.Are you familiar with..."),
        corrupting systemMessage output.
        """
        from vaudeville.server import CLASSIFY_MAX_TOKENS

        captured: dict[str, int] = {}

        class RecordingBackend:
            def classify(self, prompt: str, max_tokens: int) -> str:  # noqa: ARG002
                captured["max_tokens"] = max_tokens
                return "VERDICT: clean\nREASON: ok."

        data = json.dumps({"prompt": "test"}).encode() + b"\n"
        handle_request(data, RecordingBackend())
        assert captured["max_tokens"] == CLASSIFY_MAX_TOKENS
        assert CLASSIFY_MAX_TOKENS <= 35, (
            "classify budget must stay tight — loosening invites run-on REASONs"
        )


class TestHandleRequestEventLogging:
    def test_event_logger_called_on_clean(self, tmp_path: str) -> None:
        """handle_request calls event_logger.log_event for clean verdicts."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        try:
            backend = MockBackend(verdict="clean", reason="all good")
            data = json.dumps({"prompt": "test input", "rule": "no-hedging"}).encode()
            handle_request(data, backend, event_logger=el)
            time.sleep(0.05)

            events_path = Path(logs_dir) / "events.jsonl"
            lines = events_path.read_text().strip().splitlines()
            assert len(lines) == 1
            evt = json.loads(lines[0])
            assert evt["rule"] == "no-hedging"
            assert evt["verdict"] == "clean"
            assert evt["prompt_chars"] == len("test input")
        finally:
            el.close()

    def test_event_logger_called_on_violation(self, tmp_path: str) -> None:
        """Violations log to both events and violations files."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        try:
            backend = MockBackend(verdict="violation", reason="bad stuff")
            data = json.dumps(
                {"prompt": "bad input", "rule": "no-slop", "input_text": "bad input"}
            ).encode()
            handle_request(data, backend, event_logger=el)
            time.sleep(0.05)

            violations_path = Path(logs_dir) / "violations.jsonl"
            lines = violations_path.read_text().strip().splitlines()
            assert len(lines) == 1
            v = json.loads(lines[0])
            assert v["rule"] == "no-slop"
            assert v["verdict"] == "violation"
            assert v["reason"] == "bad stuff"
            assert v["input_snippet"] == "bad input"
        finally:
            el.close()

    def test_input_text_used_as_snippet_when_present(self, tmp_path: str) -> None:
        """input_text field is stored as input_snippet instead of the full prompt."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        try:
            backend = MockBackend(verdict="clean", reason="ok")
            data = json.dumps(
                {
                    "prompt": "RULE TEMPLATE claude said: hello there",
                    "rule": "test",
                    "input_text": "hello there",
                }
            ).encode()
            handle_request(data, backend, event_logger=el)
            time.sleep(0.05)

            events_path = Path(logs_dir) / "events.jsonl"
            evt = json.loads(events_path.read_text().strip())
            assert evt["input_snippet"] == "hello there"
        finally:
            el.close()

    def test_no_event_logger_still_works(self) -> None:
        """handle_request works without event_logger (backward compat)."""
        backend = MockBackend(verdict="clean", reason="ok")
        data = json.dumps({"prompt": "test"}).encode()
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "clean"

    def test_rule_defaults_to_empty(self, tmp_path: str) -> None:
        """Missing rule field in request defaults to empty string."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        try:
            backend = MockBackend(verdict="clean", reason="ok")
            data = json.dumps({"prompt": "no rule"}).encode()
            handle_request(data, backend, event_logger=el)
            time.sleep(0.05)

            events_path = Path(logs_dir) / "events.jsonl"
            evt = json.loads(events_path.read_text().strip())
            assert evt["rule"] == ""
        finally:
            el.close()

    def test_latency_logged(self, tmp_path: str) -> None:
        """Event includes positive latency_ms."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        try:
            backend = MockBackend(verdict="clean", reason="ok")
            data = json.dumps({"prompt": "test"}).encode()
            handle_request(data, backend, event_logger=el)
            time.sleep(0.05)

            events_path = Path(logs_dir) / "events.jsonl"
            evt = json.loads(events_path.read_text().strip())
            assert evt["latency_ms"] >= 0
        finally:
            el.close()

    def test_error_path_no_event_logged(self, tmp_path: str) -> None:
        """Malformed requests fail open and do NOT log events."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        try:
            backend = MockBackend()
            handle_request(b"not-json\n", backend, event_logger=el)
            time.sleep(0.05)

            events_path = Path(logs_dir) / "events.jsonl"
            assert not events_path.exists() or events_path.read_text().strip() == ""
        finally:
            el.close()


class TestDaemonEventLoggerWiring:
    def test_daemon_passes_event_logger(self, tmp_path: str) -> None:
        """VaudevilleDaemon stores event_logger and passes it to handle_request."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig("/tmp/test.sock", "/tmp/test.pid", plugin_root),
            event_logger=el,
        )
        assert daemon._event_logger is el
        el.close()

    def test_daemon_cleanup_closes_logger(self, tmp_path: str) -> None:
        """_cleanup() closes the event logger sinks."""
        from pathlib import Path

        logs_dir = str(Path(tmp_path) / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        assert el._events_id is not None

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sock = str(Path(tmp_path) / "test.sock")
        pid = str(Path(tmp_path) / "test.pid")
        Path(pid).touch()
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(sock, pid, plugin_root),
            event_logger=el,
        )
        daemon._cleanup()
        assert el._events_id is None
        assert el._violations_id is None

    def test_daemon_none_event_logger_default(self) -> None:
        """Daemon defaults to None event_logger."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig("/tmp/test.sock", "/tmp/test.pid", plugin_root),
        )
        assert daemon._event_logger is None


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
            backend,
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
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
            payload = json.dumps({"prompt": "All good."}).encode() + b"\n"
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

    def test_oversized_payload_returns_fail_open(self) -> None:
        """Daemon drops payloads exceeding MAX_REQUEST_SIZE and fails open."""
        import tempfile
        import os
        from vaudeville.server.daemon import MAX_REQUEST_SIZE

        with tempfile.NamedTemporaryFile(
            suffix=".sock", dir=tempfile.gettempdir(), delete=False
        ) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(
            suffix=".pid", dir=tempfile.gettempdir(), delete=False
        ) as f:
            pid_file = f.name
        os.unlink(socket_path)

        backend = MockBackend(verdict="violation", reason="should not reach backend")
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=True
        ) as vf:
            version_file = vf.name
        daemon = VaudevilleDaemon(
            backend,
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
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
            # Send payload larger than MAX_REQUEST_SIZE with no newline
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
        assert backend.calls == []  # backend should never be called
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
            SlowBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
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
                payload = json.dumps({"prompt": "test"}).encode() + b"\n"
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
        daemon = VaudevilleDaemon(
            backend, DaemonConfig(socket_path, pid_file, plugin_root)
        )

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
            MockBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
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


class TestCachedInferenceRouting:
    """Tests for _run_inference prefix_len routing and handle_request integration."""

    def test_prefix_len_zero_uses_uncached_logprobs(self) -> None:
        """prefix_len=0 falls back to classify_with_logprobs (existing path)."""
        backend = MockBackend(
            verdict="clean",
            reason="uncached",
            logprobs={"clean": -0.1, "violation": -3.0},
        )
        data = json.dumps({"prompt": "test prompt"}).encode() + b"\n"
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "clean"
        assert len(backend.calls) == 1

    def test_prefix_len_zero_default_when_absent(self) -> None:
        """Request without prefix_len field uses uncached path."""
        backend = MockBackend(verdict="violation", reason="no prefix")
        data = json.dumps({"prompt": "test"}).encode() + b"\n"
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "violation"

    def test_prefix_len_routes_to_classify_cached_with_logprobs(self) -> None:
        """prefix_len > 0 routes to classify_cached_with_logprobs when available."""
        cached_calls: list[tuple[str, int]] = []

        class CachedLogprobBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                raise AssertionError("Should not call uncached classify")

            def classify_cached(self, prompt: str, prefix_len: int) -> str:  # noqa: ARG002
                raise AssertionError("Should prefer logprob variant")

            def classify_cached_with_logprobs(
                self,
                prompt: str,
                prefix_len: int,
            ) -> ClassifyResult:
                cached_calls.append((prompt, prefix_len))
                return ClassifyResult(
                    text="VERDICT: clean\nREASON: cached logprobs",
                    logprobs={"clean": -0.05, "violation": -4.0},
                )

        data = (
            json.dumps({"prompt": "full prompt text", "prefix_len": 42}).encode()
            + b"\n"
        )
        response = json.loads(handle_request(data, CachedLogprobBackend()))
        assert response["verdict"] == "clean"
        assert len(cached_calls) == 1
        assert cached_calls[0] == ("full prompt text", 42)

    def test_prefix_len_routes_to_classify_cached_without_logprobs(self) -> None:
        """prefix_len > 0 routes to classify_cached when logprobs variant is absent."""
        cached_calls: list[tuple[str, int]] = []

        class CachedOnlyBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                raise AssertionError("Should not call uncached classify")

            def classify_cached(
                self,
                prompt: str,
                prefix_len: int,
            ) -> str:
                cached_calls.append((prompt, prefix_len))
                return "VERDICT: violation\nREASON: cached plain"

        data = json.dumps({"prompt": "some prompt", "prefix_len": 10}).encode() + b"\n"
        response = json.loads(handle_request(data, CachedOnlyBackend()))
        assert response["verdict"] == "violation"
        assert response["confidence"] == 0.0  # no logprobs → zero confidence
        assert len(cached_calls) == 1
        assert cached_calls[0] == ("some prompt", 10)

    def test_prefix_len_falls_back_when_no_cached_methods(self) -> None:
        """prefix_len > 0 falls back to uncached path when backend lacks cached methods."""

        class PlainBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                return "VERDICT: clean\nREASON: plain fallback"

        data = json.dumps({"prompt": "test", "prefix_len": 5}).encode() + b"\n"
        response = json.loads(handle_request(data, PlainBackend()))
        assert response["verdict"] == "clean"
        assert response["confidence"] == 0.0

    def test_run_inference_prefers_cached_logprobs_over_cached_plain(self) -> None:
        """When both classify_cached and classify_cached_with_logprobs exist, prefer logprobs."""
        from vaudeville.server._handlers import _run_inference

        route_taken: list[str] = []

        class DualCachedBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                route_taken.append("uncached")
                return "VERDICT: clean\nREASON: uncached"

            def classify_cached(self, prompt: str, prefix_len: int) -> str:  # noqa: ARG002
                route_taken.append("cached_plain")
                return "VERDICT: clean\nREASON: cached plain"

            def classify_cached_with_logprobs(
                self,
                prompt: str,
                prefix_len: int,
            ) -> ClassifyResult:  # noqa: ARG002
                route_taken.append("cached_logprobs")
                return ClassifyResult(
                    text="VERDICT: clean\nREASON: cached logprobs",
                    logprobs={"clean": -0.1},
                )

        _run_inference(DualCachedBackend(), "prompt", prefix_len=20)
        assert route_taken == ["cached_logprobs"]

    def test_run_inference_uncached_logprobs_when_prefix_zero(self) -> None:
        """prefix_len=0 uses classify_with_logprobs even when cached methods exist."""
        from vaudeville.server._handlers import _run_inference

        route_taken: list[str] = []

        class FullBackend(LogprobBackend):
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                route_taken.append("uncached_plain")
                return "VERDICT: clean\nREASON: ok"

            def classify_with_logprobs(
                self,
                prompt: str,
                max_tokens: int = 50,
            ) -> ClassifyResult:  # noqa: ARG002
                route_taken.append("uncached_logprobs")
                return ClassifyResult(
                    text="VERDICT: clean\nREASON: ok",
                    logprobs={"clean": -0.1},
                )

            def classify_cached_with_logprobs(
                self,
                prompt: str,
                prefix_len: int,
            ) -> ClassifyResult:  # noqa: ARG002
                route_taken.append("cached_logprobs")
                return ClassifyResult(
                    text="VERDICT: clean\nREASON: ok",
                    logprobs={"clean": -0.1},
                )

        _run_inference(FullBackend(), "prompt", prefix_len=0)
        assert route_taken == ["uncached_logprobs"]


class TestConfidenceScoring:
    def test_response_includes_confidence(self) -> None:
        backend = MockBackend(
            verdict="violation",
            reason="test",
            logprobs={"violation": -0.2, "clean": -2.5},
        )
        data = json.dumps({"prompt": "test"}).encode() + b"\n"
        response = json.loads(handle_request(data, backend))
        assert "confidence" in response
        assert 0.0 <= response["confidence"] <= 1.0

    def test_fallback_when_backend_lacks_logprobs(self) -> None:
        """Backend without classify_with_logprobs falls back gracefully."""

        class PlainBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                return "VERDICT: clean\nREASON: ok"

        data = json.dumps({"prompt": "test"}).encode() + b"\n"
        response = json.loads(handle_request(data, PlainBackend()))
        assert response["verdict"] == "clean"
        assert response["confidence"] == 0.0


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
    platform.system() != "Darwin"
    or platform.machine() != "arm64"
    or importlib.util.find_spec("mlx_lm") is None,
    reason="MLX only available on Apple Silicon with mlx_lm installed",
)
class TestMLXImportSmoke:
    """Verify real mlx_lm imports resolve — catches API drift."""

    def test_mlx_backend_imports_resolve(self) -> None:
        """MLXBackend.__init__ imports must not raise ImportError."""
        try:
            from mlx_lm import stream_generate  # noqa: F401
            from mlx_lm.generate import generate_step  # noqa: F401
        except ImportError:
            pytest.skip("mlx-lm not installed")

    def test_generate_step_signature_has_sampler(self) -> None:
        """generate_step must accept sampler= (not temp=)."""
        import inspect

        try:
            from mlx_lm.generate import generate_step
        except ImportError:
            pytest.skip("mlx-lm not installed")

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

    def test_classify_with_logprobs_sends_verdict_prefill(self) -> None:
        """classify_with_logprobs must send assistant prefill and return VERDICT: prefix."""
        from unittest.mock import MagicMock, patch

        fake_response = {
            "choices": [
                {
                    "message": {"content": " clean"},
                    "logprobs": {
                        "content": [
                            {
                                "top_logprobs": [
                                    {"token": "clean", "logprob": -0.1},
                                    {"token": "violation", "logprob": -3.0},
                                ]
                            }
                        ]
                    },
                }
            ]
        }
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = fake_response

        with patch(
            "vaudeville.server.gguf_backend.GGUFBackend.__init__", return_value=None
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend.__new__(GGUFBackend)
            backend._llm = mock_llm

            result = backend.classify_with_logprobs("test prompt")

        # Text must start with VERDICT: prefix
        assert result.text.startswith("VERDICT:"), (
            f"Expected 'VERDICT:' prefix, got: {result.text!r}"
        )
        # Logprobs must come from position 0 only
        assert "clean" in result.logprobs
        assert result.logprobs["clean"] == -0.1
        # Request must include assistant prefill message
        call_messages = mock_llm.create_chat_completion.call_args[1]["messages"]
        assert call_messages[-1] == {"role": "assistant", "content": "VERDICT:"}, (
            "Last message must be assistant prefill with 'VERDICT:'"
        )
