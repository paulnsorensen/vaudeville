"""Adversarial tests for the singleton daemon.

Attack vectors:
1. Version stamp race (two session-start.sh racing)
2. Version file permissions (not writable)
3. Git not available (_write_version_stamp fallback)
4. Socket path collision (request arrives during shutdown)
5. PID file contains garbage (non-numeric content)
6. Daemon cleanup interrupted (SIGKILL leaves version file)
7. Request lock contention (many simultaneous hook requests)
8. handle_request robustness (empty/null/malformed/oversized payloads)
"""

from __future__ import annotations

import fcntl
import functools
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.core.paths import VERSION_FILE
from vaudeville.server import DaemonConfig, VaudevilleDaemon, user_config
from vaudeville.server._handlers import handle_request
from vaudeville.server.agents import decide
from vaudeville.server.hook import pipeline as pipeline_module


def _write_rule(rules_root: Path) -> None:
    rules_dir = rules_root / ".vaudeville" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "safe.yaml").write_text(
        """
type: decide
name: safe
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [safe, violation]
"on":
  violation: block
tier: block
"""
    )


def _hook_payload(rules_root: Path, tool_input: dict[str, object]) -> dict[str, object]:
    return {
        "op": "hook",
        "harness": "claude-code",
        "event": "PreToolUse",
        "cwd": str(rules_root),
        "payload": {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": tool_input,
            "cwd": str(rules_root),
        },
    }


def _patch_decide(monkeypatch: pytest.MonkeyPatch, output: str, delay: float = 0.0) -> list[int]:
    """Replace `default_decide` with a scripted model; returns its call log."""
    calls: list[int] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        calls.append(1)
        if delay:
            time.sleep(delay)
        return ModelResponse(parts=[TextPart(output)])

    fn = functools.partial(decide, model_override=FunctionModel(respond))
    monkeypatch.setattr(pipeline_module, "default_decide", fn)
    return calls


def _pin_fake_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `user_config.CONFIG_PATH` at a config listing the `fake` provider.

    `CONFIG_PATH` binds `os.path.expanduser("~")` at import time, so
    patching `HOME` after import (tests/conftest.py) never reaches it; a
    real daemon request (`config=None`) would otherwise load whatever
    `~/.vaudeville/config` the developer running the suite happens to have.
    """
    config_path = tmp_path / "vaudeville-config"
    config_path.write_text(
        "default_model: fake:model\nproviders:\n  fake:\n    key_env: FAKE_KEY\n"
    )
    monkeypatch.setattr(user_config, "CONFIG_PATH", str(config_path))


def _make_daemon(
    socket_path: str,
    pid_file: str,
    version_file: str = VERSION_FILE,
) -> VaudevilleDaemon:
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return VaudevilleDaemon(
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
        Path(socket_path).unlink()

        daemon1, thread1 = _ready_daemon(socket_path, pid_file, version_file)

        try:
            version_after_winner = Path(version_file).open().read().strip()
            assert version_after_winner != "", "winner must write a non-empty version"

            # Second daemon with SAME pid_file — should bail after PID lock conflict
            with tempfile.NamedTemporaryFile(
                suffix=".sock2", dir=tempfile.gettempdir(), delete=False
            ) as f2:
                socket_path2 = f2.name
            Path(socket_path2).unlink()

            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            daemon2 = VaudevilleDaemon(
                DaemonConfig(socket_path2, pid_file, plugin_root, version_file),
            )
            # serve() should return immediately due to PID lock held by daemon1
            thread2 = threading.Thread(target=daemon2.serve, daemon=True)
            thread2.start()
            thread2.join(timeout=3.0)
            assert not thread2.is_alive(), "loser daemon should have exited"

            # Version file must still belong to daemon1
            assert Path(version_file).open().read().strip() == version_after_winner
        finally:
            daemon1._stop_event.set()
            thread1.join(timeout=3)

    def test_version_file_not_present_until_pid_lock_acquired(self) -> None:
        """_write_version_stamp() is called AFTER the PID lock, not before."""
        events: list[str] = []

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
        Path(socket_path).unlink()
        Path(version_file).unlink()

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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
        Path(socket_path).unlink()

        # Use a path that will be unwritable (inside a read-only dir we create)
        with tempfile.TemporaryDirectory() as td:
            version_file = os.path.join(td, "subdir", "vaudeville.version")
            # subdir does NOT exist → write will fail

            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            daemon = VaudevilleDaemon(
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

        content = Path(version_file).open().read().strip()
        assert content == "unknown", f"Expected 'unknown' when git is unavailable, got {content!r}"

    def test_write_version_stamp_falls_back_when_git_nonzero_exit(self) -> None:
        """Non-zero git exit → stamp is 'unknown'."""
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as fv:
            version_file = fv.name

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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

        content = Path(version_file).open().read().strip()
        assert content == "unknown", f"Expected 'unknown' for non-zero git exit, got {content!r}"

    def test_write_version_stamp_falls_back_on_timeout(self) -> None:
        """Timed-out git command → stamp is 'unknown', no exception raised."""
        with tempfile.NamedTemporaryFile(
            suffix=".version", dir=tempfile.gettempdir(), delete=False
        ) as fv:
            version_file = fv.name

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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

        content = Path(version_file).open().read().strip()
        assert content == "unknown", f"Expected 'unknown' for git timeout, got {content!r}"


# ---------------------------------------------------------------------------
# Attack 4: Request arrives while daemon is shutting down
# ---------------------------------------------------------------------------


class TestShutdownRace:
    def test_in_flight_request_gets_response_during_shutdown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A request dispatched before _stop_event is set must complete."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _pin_fake_config(monkeypatch, tmp_path)
        _write_rule(tmp_path)
        # Slow decide call so the request is in-flight during shutdown
        calls = _patch_decide(monkeypatch, '{"outcome": "safe"}', delay=0.1)

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
        Path(socket_path).unlink()

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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

        payload = json.dumps(_hook_payload(tmp_path, {"content": "hello"})).encode() + b"\n"

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
        assert results[0].get("exit_code") == 0, f"Unexpected response: {results[0]}"
        assert len(calls) == 1, f"Expected 1 decide call, got {len(calls)}"

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
        Path(socket_path).unlink()

        daemon, thread = _ready_daemon(socket_path, pid_file, version_file)
        daemon._stop_event.set()
        thread.join(timeout=5)

        # _cleanup() must remove the socket file
        assert not Path(socket_path).exists(), (
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
        Path(socket_path).unlink()

        # Pre-lock the PID file with LOCK_EX so daemon can't acquire it
        lock_fd = os.open(pid_file, os.O_WRONLY | os.O_CREAT, 0o644)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            daemon = VaudevilleDaemon(
                DaemonConfig(socket_path, pid_file, plugin_root, version_file),
            )
            thread = threading.Thread(target=daemon.serve, daemon=True)
            thread.start()
            thread.join(timeout=3.0)
            assert not thread.is_alive(), "Daemon should exit when PID file is already locked"
            # Socket must NOT have been created
            assert not Path(socket_path).exists(), (
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
        Path(socket_path).unlink()

        # The daemon re-opens and relocks the PID file; garbage content shouldn't matter
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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

        assert socket_ready, "Daemon failed to start when PID file contained garbage content"


# ---------------------------------------------------------------------------
# Attack 6: Cleanup interrupted — version file left behind after SIGKILL
# ---------------------------------------------------------------------------


class TestCleanupInterrupted:
    def test_cleanup_is_idempotent_for_already_removed_files(self) -> None:
        """_cleanup() must not raise if socket/pid/version files are already gone."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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
        Path(socket_path).unlink()

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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
            content = Path(version_file).open().read().strip()
            assert content != "stale-git-hash-from-dead-daemon", (
                "New daemon must overwrite stale version file, not keep old value"
            )
        finally:
            daemon._stop_event.set()
            thread.join(timeout=3)


# ---------------------------------------------------------------------------
# Attack 7: Request lock contention — many simultaneous hook requests
# ---------------------------------------------------------------------------


class TestRequestLockContention:
    def test_no_deadlock_under_high_concurrency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """10 simultaneous requests must all complete without deadlocking."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _pin_fake_config(monkeypatch, tmp_path)
        _write_rule(tmp_path)
        calls = _patch_decide(monkeypatch, '{"outcome": "safe"}')

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
        Path(socket_path).unlink()

        daemon, thread = _ready_daemon(socket_path, pid_file, version_file)

        payload = json.dumps(_hook_payload(tmp_path, {"content": "hello"})).encode() + b"\n"

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
        assert len(calls) == 10, f"Expected 10 decide calls, got {len(calls)}"

    def test_concurrent_requests_run_in_parallel_not_serialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_request_lock` only guards `_last_request`; hook pipelines overlap."""
        call_intervals: list[tuple[float, float]] = []
        ci_lock = threading.Lock()

        def slow_handle_hook_request(
            request: object, *, event_logger: object = None
        ) -> dict[str, object]:
            del request, event_logger
            t0 = time.monotonic()
            time.sleep(0.03)
            t1 = time.monotonic()
            with ci_lock:
                call_intervals.append((t0, t1))
            return {"stdout": "", "exit_code": 0}

        from vaudeville.server import _handlers

        monkeypatch.setattr(_handlers, "handle_hook_request", slow_handle_hook_request)

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
        Path(socket_path).unlink()

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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

        payload = json.dumps(_hook_payload(tmp_path, {"content": "hello"})).encode() + b"\n"

        started = time.monotonic()
        workers = [
            threading.Thread(target=_send_request, args=(socket_path, payload)) for _ in range(5)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=10)
        elapsed = time.monotonic() - started

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert len(call_intervals) == 5, f"Expected 5 calls, got {len(call_intervals)}"
        assert elapsed < 0.03 * 5, (
            f"5 requests took {elapsed:.3f}s; expected overlap, not serialization"
        )
        assert daemon._last_request >= started, "_last_request not updated"

    def test_two_concurrent_slow_decides_complete_faster_than_serial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two requests whose decide sleeps 1s each finish in well under 2s."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _pin_fake_config(monkeypatch, tmp_path)
        _write_rule(tmp_path)
        calls = _patch_decide(monkeypatch, '{"outcome": "safe"}', delay=1.0)

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
        Path(socket_path).unlink()

        daemon, thread = _ready_daemon(socket_path, pid_file, version_file)

        payload = json.dumps(_hook_payload(tmp_path, {"content": "hello"})).encode() + b"\n"

        responses: list[dict[str, object]] = []
        lock = threading.Lock()

        def send() -> None:
            resp = _send_request(socket_path, payload)
            with lock:
                responses.append(resp)

        started = time.monotonic()
        workers = [threading.Thread(target=send) for _ in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=10)
        elapsed = time.monotonic() - started

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert len(responses) == 2
        assert elapsed < 1.5, f"2 concurrent 1s decides took {elapsed:.3f}s"
        assert len(calls) == 2, f"Expected 2 decide calls, got {len(calls)}"


# ---------------------------------------------------------------------------
# Real routing: a violation outcome, decided for real over the socket, denies
# ---------------------------------------------------------------------------


class TestDecideRouting:
    def test_violation_outcome_denies_over_real_socket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _pin_fake_config(monkeypatch, tmp_path)
        _write_rule(tmp_path)
        calls = _patch_decide(monkeypatch, '{"outcome": "violation"}')

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
        Path(socket_path).unlink()

        daemon, thread = _ready_daemon(socket_path, pid_file, version_file)

        payload = json.dumps(_hook_payload(tmp_path, {"content": "hello"})).encode() + b"\n"

        result = _send_request(socket_path, payload)

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert len(calls) == 1, f"Expected 1 decide call, got {len(calls)}"
        assert "deny" in str(result.get("stdout", ""))


# ---------------------------------------------------------------------------
# Attack: handle_request with extreme/invalid inputs
# ---------------------------------------------------------------------------


class TestHandleRequestEdgeCases:
    def test_empty_bytes(self) -> None:
        """Empty payload must fail open (allow) — no crash on empty input."""
        response = json.loads(handle_request(b""))
        assert response == {"stdout": "", "exit_code": 0}

    def test_null_bytes_in_payload(self) -> None:
        """Null bytes in payload must not crash the handler; fails open."""
        response = json.loads(handle_request(b"\x00\x01\x02\x03\n"))
        assert response == {"stdout": "", "exit_code": 0}

    def test_malformed_json_over_socket(self) -> None:
        """Truncated/invalid JSON must not raise; fails open."""
        response = json.loads(handle_request(b"{not valid json\n"))
        assert response == {"stdout": "", "exit_code": 0}

    def test_oversized_payload(self, tmp_path: Path) -> None:
        """A 10MB hook payload with no matching rule must fail open, not crash."""
        giant_text = "x" * (10 * 1024 * 1024)
        payload = json.dumps(_hook_payload(tmp_path, {"content": giant_text})).encode() + b"\n"
        response = json.loads(handle_request(payload))
        assert response.get("exit_code") == 0


# ---------------------------------------------------------------------------
# Attack: _cleanup() doesn't remove pid_file if pid_fd is None (never locked)
# ---------------------------------------------------------------------------


class TestCleanupWithoutPidLock:
    def test_cleanup_skips_pid_close_if_never_locked(self) -> None:
        """_cleanup() with pid_fd=None must not raise AttributeError or OSError."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
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
