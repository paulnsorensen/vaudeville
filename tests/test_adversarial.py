"""Adversarial tests for the singleton daemon migration.

Attack vectors:
1. Version stamp race (two session-start.sh racing)
2. Version file permissions (not writable)
3. Git not available (_write_version_stamp fallback)
4. Socket path collision (request arrives during shutdown)
5. PID file contains garbage (non-numeric content)
6. Daemon cleanup interrupted (SIGKILL leaves version file)
7. Backend lock contention (many simultaneous classify calls)
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

from vaudeville.core.paths import VERSION_FILE
from vaudeville.server import DaemonConfig, VaudevilleDaemon, handle_request
from conftest import MockBackend


def _make_daemon(
    socket_path: str,
    pid_file: str,
    version_file: str = VERSION_FILE,
) -> VaudevilleDaemon:
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return VaudevilleDaemon(
        MockBackend(),
        DaemonConfig(socket_path, pid_file, plugin_root, version_file),
    )


def _ready_daemon(
    socket_path: str,
    pid_file: str,
    version_file: str = VERSION_FILE,
    timeout: float = 2.0,
) -> tuple[VaudevilleDaemon, threading.Thread]:
    """Spin up a daemon and block until the socket is ready."""
    daemon = _make_daemon(socket_path, pid_file, version_file)
    thread = threading.Thread(target=daemon.serve, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.1)
                probe.connect(socket_path)
            return daemon, thread
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            time.sleep(0.05)

    daemon._stop_event.set()
    raise RuntimeError(f"Daemon socket {socket_path} not ready within {timeout}s")


def _send_request(sock_path: str, payload: bytes) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3.0)
        sock.connect(sock_path)
        sock.sendall(payload)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk or b"\n" in data + chunk:
                data += chunk
                break
    result: dict[str, object] = json.loads(data.decode().strip())
    return result


# ---------------------------------------------------------------------------
# Attack 1: Version stamp race — two instances try to write VERSION_FILE
# ---------------------------------------------------------------------------


class TestVersionStampRace:
    def test_pid_lock_prevents_second_daemon_from_writing_version(self) -> None:
        """Second daemon attempt must not overwrite version file of winner."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f1,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f1.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)

        daemon1, thread1 = _ready_daemon(socket_path, pid_file, version_file)

        try:
            version_after_winner = open(version_file).read().strip()
            assert version_after_winner != "", "winner must write a non-empty version"

            # Second daemon with SAME pid_file — should bail after PID lock conflict
            with tempfile.NamedTemporaryFile(
                suffix=".sock2", dir=tempfile.gettempdir(), delete=False
            ) as f2:
                socket_path2 = f2.name
            os.unlink(socket_path2)

            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            daemon2 = VaudevilleDaemon(
                MockBackend(),
                DaemonConfig(socket_path2, pid_file, plugin_root, version_file),
            )
            # serve() should return immediately due to PID lock held by daemon1
            thread2 = threading.Thread(target=daemon2.serve, daemon=True)
            thread2.start()
            thread2.join(timeout=3.0)
            assert not thread2.is_alive(), "loser daemon should have exited"

            # Version file must still belong to daemon1
            assert open(version_file).read().strip() == version_after_winner
        finally:
            daemon1._stop_event.set()
            thread1.join(timeout=3)

    def test_version_file_not_present_until_pid_lock_acquired(self) -> None:
        """_write_version_stamp() is called AFTER the PID lock, not before."""
        events: list[str] = []

        class TracingBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:
                return "VERDICT: clean\nREASON: ok"

        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)
        os.unlink(version_file)

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            TracingBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
        )

        original_write = daemon._write_version_stamp

        def patched_write() -> None:
            # At this point PID lock is held; version file must NOT exist yet
            events.append("write_version_called")
            original_write()

        daemon._write_version_stamp = patched_write  # type: ignore[method-assign]
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                time.sleep(0.05)

        daemon._stop_event.set()
        thread.join(timeout=3)

        assert "write_version_called" in events, "_write_version_stamp never called"


# ---------------------------------------------------------------------------
# Attack 2: Version file not writable
# ---------------------------------------------------------------------------


class TestVersionFilePermissions:
    def test_write_version_stamp_fails_open_on_permission_error(self) -> None:
        """If VERSION_FILE is not writable, serve() must not crash."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
        ):
            socket_path = f.name
            pid_file = fp.name
        os.unlink(socket_path)

        # Use a path that will be unwritable (inside a read-only dir we create)
        with tempfile.TemporaryDirectory() as td:
            version_file = os.path.join(td, "subdir", "vaudeville.version")
            # subdir does NOT exist → write will fail

            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            daemon = VaudevilleDaemon(
                MockBackend(),
                DaemonConfig(socket_path, pid_file, plugin_root, version_file),
            )

            # Serve should still bind the socket despite write failure
            thread = threading.Thread(target=daemon.serve, daemon=True)
            thread.start()

            socket_ready = False
            for _ in range(40):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.settimeout(0.1)
                        probe.connect(socket_path)
                    socket_ready = True
                    break
                except (ConnectionRefusedError, FileNotFoundError, OSError):
                    time.sleep(0.05)

            daemon._stop_event.set()
            thread.join(timeout=3)

            # The daemon crashed instead of serving — this is a BUG
            assert socket_ready, (
                "Daemon crashed when VERSION_FILE directory doesn't exist — "
                "_write_version_stamp must handle write errors gracefully"
            )


# ---------------------------------------------------------------------------
# Attack 3: git not available
# ---------------------------------------------------------------------------


class TestGitNotAvailable:
    def test_write_version_stamp_falls_back_to_unknown_when_git_missing(self) -> None:
        """If git is unavailable, version stamp must be 'unknown', not an error."""
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as fv:
            version_file = fv.name

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(
                "/tmp/_test_git_na.sock",
                "/tmp/_test_git_na.pid",
                plugin_root,
                version_file,
            ),
        )

        # Simulate git not found by making the subprocess raise OSError
        with patch("subprocess.run", side_effect=OSError("git not found")):
            daemon._write_version_stamp()

        content = open(version_file).read().strip()
        assert content == "unknown", (
            f"Expected 'unknown' when git is unavailable, got {content!r}"
        )

    def test_write_version_stamp_falls_back_when_git_nonzero_exit(self) -> None:
        """Non-zero git exit → stamp is 'unknown'."""
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as fv:
            version_file = fv.name

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(
                "/tmp/_test_git_fail.sock",
                "/tmp/_test_git_fail.pid",
                plugin_root,
                version_file,
            ),
        )

        fake_result = MagicMock()
        fake_result.returncode = 128
        fake_result.stdout = ""

        with patch("subprocess.run", return_value=fake_result):
            daemon._write_version_stamp()

        content = open(version_file).read().strip()
        assert content == "unknown", (
            f"Expected 'unknown' for non-zero git exit, got {content!r}"
        )

    def test_write_version_stamp_falls_back_on_timeout(self) -> None:
        """Timed-out git command → stamp is 'unknown', no exception raised."""
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as fv:
            version_file = fv.name

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(
                "/tmp/_test_git_timeout.sock",
                "/tmp/_test_git_timeout.pid",
                plugin_root,
                version_file,
            ),
        )

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            daemon._write_version_stamp()

        content = open(version_file).read().strip()
        assert content == "unknown", (
            f"Expected 'unknown' for git timeout, got {content!r}"
        )


# ---------------------------------------------------------------------------
# Attack 4: Request arrives while daemon is shutting down
# ---------------------------------------------------------------------------


class TestShutdownRace:
    def test_in_flight_request_gets_response_during_shutdown(self) -> None:
        """A request dispatched before _stop_event is set must complete."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)

        # Slow backend so the request is in-flight during shutdown
        class SlowBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:
                time.sleep(0.1)
                return "VERDICT: clean\nREASON: ok"

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            SlowBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
        )
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                time.sleep(0.05)

        payload = (
            json.dumps(
                {"rule": "violation-detector", "input": {"text": "test"}}
            ).encode()
            + b"\n"
        )

        results: list[dict[str, object]] = []
        errors: list[Exception] = []

        def send() -> None:
            try:
                results.append(_send_request(socket_path, payload))
            except Exception as e:
                errors.append(e)

        req_thread = threading.Thread(target=send)
        req_thread.start()

        # Signal shutdown immediately after dispatch
        time.sleep(0.01)
        daemon._stop_event.set()

        req_thread.join(timeout=5)
        thread.join(timeout=5)

        # In-flight request must have succeeded — no connection error
        assert not errors, f"In-flight request raised: {errors}"
        assert results, "In-flight request produced no response"
        assert results[0].get("verdict") in ("clean", "violation"), (
            f"Unexpected verdict: {results[0]}"
        )

    def test_new_connection_rejected_after_socket_closed(self) -> None:
        """After daemon stops, the socket must be gone (no stale file)."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)

        daemon, thread = _ready_daemon(socket_path, pid_file, version_file)
        daemon._stop_event.set()
        thread.join(timeout=5)

        # _cleanup() must remove the socket file
        assert not os.path.exists(socket_path), (
            "Socket file not removed on shutdown — stale socket left behind"
        )


# ---------------------------------------------------------------------------
# Attack 5: PID file contains garbage
# ---------------------------------------------------------------------------


class TestPidFileGarbage:
    def test_serve_exits_when_pid_file_locked_by_another(self) -> None:
        """If another process already holds the PID lock, serve() must exit silently."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)

        # Pre-lock the PID file with LOCK_EX so daemon can't acquire it
        lock_fd = os.open(pid_file, os.O_WRONLY | os.O_CREAT, 0o644)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            daemon = VaudevilleDaemon(
                MockBackend(),
                DaemonConfig(socket_path, pid_file, plugin_root, version_file),
            )
            thread = threading.Thread(target=daemon.serve, daemon=True)
            thread.start()
            thread.join(timeout=3.0)
            assert not thread.is_alive(), (
                "Daemon should exit when PID file is already locked"
            )
            # Socket must NOT have been created
            assert not os.path.exists(socket_path), (
                "Daemon bound socket even though PID lock was held by another process"
            )
        finally:
            os.close(lock_fd)

    def test_pid_file_with_garbage_content_still_allows_serve(self) -> None:
        """PID file with non-numeric content must not crash daemon startup."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False, mode="w"
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
            fp.write("not-a-pid\x00\xff garbage\n")
        os.unlink(socket_path)

        # The daemon re-opens and relocks the PID file; garbage content shouldn't matter
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
        )
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        socket_ready = False
        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                socket_ready = True
                break
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                time.sleep(0.05)

        daemon._stop_event.set()
        thread.join(timeout=3)

        assert socket_ready, (
            "Daemon failed to start when PID file contained garbage content"
        )


# ---------------------------------------------------------------------------
# Attack 6: Cleanup interrupted — version file left behind after SIGKILL
# ---------------------------------------------------------------------------


class TestCleanupInterrupted:
    def test_cleanup_is_idempotent_for_already_removed_files(self) -> None:
        """_cleanup() must not raise if socket/pid/version files are already gone."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(
                "/tmp/_nonexistent_test.sock",
                "/tmp/_nonexistent_test.pid",
                plugin_root,
                "/tmp/_nonexistent_test.version",
            ),
        )
        # No files exist — must not raise
        daemon._cleanup()
        daemon._cleanup()  # second call also must not raise

    def test_stale_version_file_does_not_prevent_new_daemon(self) -> None:
        """A leftover VERSION_FILE from a crashed daemon must be overwritten."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False, mode="w"
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
            fv.write("stale-git-hash-from-dead-daemon\n")
        os.unlink(socket_path)

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
        )
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                time.sleep(0.05)

        try:
            content = open(version_file).read().strip()
            assert content != "stale-git-hash-from-dead-daemon", (
                "New daemon must overwrite stale version file, not keep old value"
            )
        finally:
            daemon._stop_event.set()
            thread.join(timeout=3)


# ---------------------------------------------------------------------------
# Attack 7: Backend lock contention — many simultaneous classify calls
# ---------------------------------------------------------------------------


class TestBackendLockContention:
    def test_no_deadlock_under_high_concurrency(self) -> None:
        """10 simultaneous requests must all complete without deadlocking."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)

        daemon, thread = _ready_daemon(socket_path, pid_file, version_file)

        payload = (
            json.dumps(
                {"rule": "violation-detector", "input": {"text": "test text"}}
            ).encode()
            + b"\n"
        )

        responses: list[dict[str, object]] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def send() -> None:
            try:
                resp = _send_request(socket_path, payload)
                with lock:
                    responses.append(resp)
            except Exception as e:
                with lock:
                    errors.append(e)

        workers = [threading.Thread(target=send) for _ in range(10)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=10)

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert not errors, f"Concurrent requests raised errors: {errors}"
        assert len(responses) == 10, f"Expected 10 responses, got {len(responses)}"

    def test_backend_lock_held_for_duration_of_classify(self) -> None:
        """Backend calls must not overlap (lock must serialize them end-to-end)."""
        with (
            tempfile.NamedTemporaryFile(
                suffix=".sock", dir=tempfile.gettempdir(), delete=False
            ) as f,
            tempfile.NamedTemporaryFile(
                suffix=".pid", dir=tempfile.gettempdir(), delete=False
            ) as fp,
            tempfile.NamedTemporaryFile(
                suffix=".version", dir=tempfile.gettempdir(), delete=False
            ) as fv,
        ):
            socket_path = f.name
            pid_file = fp.name
            version_file = fv.name
        os.unlink(socket_path)

        call_intervals: list[tuple[float, float]] = []
        ci_lock = threading.Lock()

        class TimingBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:
                t0 = time.monotonic()
                time.sleep(0.03)
                t1 = time.monotonic()
                with ci_lock:
                    call_intervals.append((t0, t1))
                return "VERDICT: clean\nREASON: ok"

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            TimingBackend(),
            DaemonConfig(socket_path, pid_file, plugin_root, version_file),
        )
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        for _ in range(40):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    probe.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                time.sleep(0.05)

        payload = (
            json.dumps(
                {"rule": "violation-detector", "input": {"text": "test"}}
            ).encode()
            + b"\n"
        )

        workers = [
            threading.Thread(target=_send_request, args=(socket_path, payload))
            for _ in range(5)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=10)

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert len(call_intervals) == 5, f"Expected 5 calls, got {len(call_intervals)}"
        sorted_intervals = sorted(call_intervals)
        for i in range(len(sorted_intervals) - 1):
            end_i = sorted_intervals[i][1]
            start_next = sorted_intervals[i + 1][0]
            assert end_i <= start_next + 0.01, (
                f"Backend calls overlapped: [{sorted_intervals[i]}] and "
                f"[{sorted_intervals[i + 1]}] — _backend_lock not protecting correctly"
            )


# ---------------------------------------------------------------------------
# Attack: handle_request with extreme/invalid inputs
# ---------------------------------------------------------------------------


class TestHandleRequestEdgeCases:
    def test_empty_bytes(self) -> None:
        """Empty payload must return clean (fail-open)."""
        backend = MockBackend()
        response = json.loads(handle_request(b"", backend))
        assert response["verdict"] == "clean"

    def test_null_bytes_in_payload(self) -> None:
        """Null bytes in payload must not crash the handler."""
        backend = MockBackend()
        response = json.loads(handle_request(b"\x00\x01\x02\x03\n", backend))
        assert response["verdict"] == "clean"

    def test_oversized_payload(self) -> None:
        """10MB payload must not cause an unhandled exception."""
        backend = MockBackend()
        giant_text = "x" * (10 * 1024 * 1024)
        payload = json.dumps({"prompt": giant_text}).encode() + b"\n"
        response = json.loads(handle_request(payload, backend))
        assert "verdict" in response

    def test_missing_prompt_key(self) -> None:
        """Payload without 'prompt' key uses empty string."""
        backend = MockBackend()
        payload = json.dumps({"other": "data"}).encode() + b"\n"
        response = json.loads(handle_request(payload, backend))
        assert "verdict" in response

    def test_backend_exception_returns_clean(self) -> None:
        """If backend raises, response must be clean (fail-open), not an exception."""

        class ExplodingBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:
                raise RuntimeError("GPU on fire")

        payload = json.dumps({"prompt": "test"}).encode() + b"\n"
        response = json.loads(handle_request(payload, ExplodingBackend()))
        assert response["verdict"] == "clean", (
            "Backend exception must return clean (fail-open)"
        )


# ---------------------------------------------------------------------------
# Attack: _cleanup() doesn't remove pid_file if pid_fd is None (never locked)
# ---------------------------------------------------------------------------


class TestCleanupWithoutPidLock:
    def test_cleanup_skips_pid_close_if_never_locked(self) -> None:
        """_cleanup() with pid_fd=None must not raise AttributeError or OSError."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(
                "/tmp/_skip_test.sock",
                "/tmp/_skip_test.pid",
                plugin_root,
                "/tmp/_skip_test.version",
            ),
        )
        assert daemon._pid_fd is None
        # Must not raise
        daemon._cleanup()
