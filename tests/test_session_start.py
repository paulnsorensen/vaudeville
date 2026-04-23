"""Tests for hooks/session-start.sh — daemon lifecycle management.

These tests exercise the shell script directly via subprocess, using a
controlled temporary environment so they never touch the real runtime
directory or model weights.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import TypedDict

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_START = os.path.join(PROJECT_ROOT, "hooks", "session-start.sh")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARCH = os.uname().machine
_OS = os.uname().sysname


def _model_cache_relpath() -> str:
    """Return the model-cache subdirectory (relative to HOME) for this platform."""
    if _OS == "Darwin" and _ARCH == "arm64":
        return ".cache/huggingface/hub/models--mlx-community--Phi-4-mini-instruct-4bit"
    return ".cache/huggingface/hub/models--microsoft--Phi-4-mini-instruct-gguf"


def _skip_if_unsupported() -> None:
    """Skip on platforms the script itself refuses to run on."""
    supported = (_OS == "Darwin" and _ARCH == "arm64") or _ARCH in (
        "x86_64",
        "aarch64",
    )
    if not supported:
        pytest.skip(f"Unsupported platform: {_OS}/{_ARCH}")


class SessionEnv(TypedDict):
    runtime_dir: pathlib.Path
    fake_home: pathlib.Path
    fake_bin: pathlib.Path
    env: dict[str, str]


@pytest.fixture()
def session_env(tmp_path: pathlib.Path) -> SessionEnv:
    """Build a minimal sandboxed environment for session-start.sh.

    * VAUDEVILLE_RUNTIME_DIR — points to a fresh temp runtime dir.
    * HOME — a temp home containing a fake (empty) model-cache directory so
      the model-cache check passes without real weights.
    * PATH — prepends a fake ``uv`` that exits 0 immediately, so no real
      daemon is ever spawned.
    * CLAUDE_ENV_FILE — removed so export_socket_path is a no-op.
    """
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o700)

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    model_cache = fake_home / _model_cache_relpath()
    model_cache.mkdir(parents=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["VAUDEVILLE_RUNTIME_DIR"] = str(runtime_dir)
    env["HOME"] = str(fake_home)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.pop("CLAUDE_ENV_FILE", None)

    return SessionEnv(
        runtime_dir=runtime_dir,
        fake_home=fake_home,
        fake_bin=fake_bin,
        env=env,
    )


def _run_session_start(
    env: dict[str, str], timeout: int = 15
) -> subprocess.CompletedProcess[bytes]:
    """Execute hooks/session-start.sh with empty stdin, capture output."""
    return subprocess.run(
        ["bash", SESSION_START],
        env=env,
        input=b"",
        capture_output=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Log-rotation tests
# ---------------------------------------------------------------------------


class TestLogRotation:
    """session-start.sh rotates ${LOG_FILE} when it exceeds 50 MB."""

    def test_log_over_50mb_rotated_to_dot_one(self, session_env: SessionEnv) -> None:
        """A 60 MB log file is moved to .log.1 before the daemon spawns."""
        _skip_if_unsupported()

        runtime_dir = session_env["runtime_dir"]
        log_file = runtime_dir / "vaudeville.log"
        # Write 60 MiB of data
        log_file.write_bytes(b"x" * (60 * 1024 * 1024))

        result = _run_session_start(session_env["env"])

        assert result.returncode == 0, result.stderr.decode()
        rotated = runtime_dir / "vaudeville.log.1"
        assert rotated.exists(), (
            "Expected vaudeville.log.1 to exist after rotation of 60 MB log"
        )
        # Rotation message must appear in stderr
        assert b"Log rotated" in result.stderr

    def test_log_under_50mb_not_rotated(self, session_env: SessionEnv) -> None:
        """A 10 MB log file must NOT be rotated."""
        _skip_if_unsupported()

        runtime_dir = session_env["runtime_dir"]
        log_file = runtime_dir / "vaudeville.log"
        log_file.write_bytes(b"x" * (10 * 1024 * 1024))

        result = _run_session_start(session_env["env"])

        assert result.returncode == 0, result.stderr.decode()
        rotated = runtime_dir / "vaudeville.log.1"
        assert not rotated.exists(), "Small log file should not have been rotated"

    def test_no_log_file_is_fine(self, session_env: SessionEnv) -> None:
        """No log file present — rotation code is a no-op."""
        _skip_if_unsupported()

        runtime_dir = session_env["runtime_dir"]
        result = _run_session_start(session_env["env"])

        assert result.returncode == 0, result.stderr.decode()
        assert not (runtime_dir / "vaudeville.log.1").exists()

    def test_existing_dot_one_overwritten_on_second_rotation(
        self, session_env: SessionEnv
    ) -> None:
        """If .log.1 already exists from a previous rotation it is replaced."""
        _skip_if_unsupported()

        runtime_dir = session_env["runtime_dir"]
        log_file = runtime_dir / "vaudeville.log"
        old_rotated = runtime_dir / "vaudeville.log.1"
        old_rotated.write_bytes(b"old")
        log_file.write_bytes(b"y" * (60 * 1024 * 1024))

        result = _run_session_start(session_env["env"])

        assert result.returncode == 0, result.stderr.decode()
        assert old_rotated.exists()
        # The new .1 must be the 60 MB file, not the old tiny sentinel
        assert old_rotated.stat().st_size > 1024


# ---------------------------------------------------------------------------
# Restart-race / socket-polling tests
# ---------------------------------------------------------------------------


class TestRestartRaceDaemonWarning:
    """After spawning a daemon that never binds its socket, warn to stderr."""

    def test_daemon_no_socket_emits_warning_and_exits_0(
        self, session_env: SessionEnv
    ) -> None:
        """Fake daemon (exits immediately, no socket) triggers the 3-second
        warning message.  The hook must still exit 0 (fail-open).
        """
        _skip_if_unsupported()

        result = _run_session_start(session_env["env"])

        # Fail-open: exit 0 even when daemon is absent
        assert result.returncode == 0, result.stderr.decode()
        stderr = result.stderr.decode()
        assert "daemon did not come up" in stderr, (
            f"Expected 'daemon did not come up' warning in stderr.\nstderr={stderr!r}"
        )

    @pytest.mark.skipif(
        sys.platform == "darwin", reason="flock(1) unavailable on macOS"
    )
    def test_restart_race_with_flock_emits_warning(
        self, session_env: SessionEnv
    ) -> None:
        """Simulate restart race: old daemon holds flock on PID_FILE.

        Setup:
        - Start a ``sleep`` subprocess whose PID is written to PID_FILE (it
          simulates the running old daemon).
        - A second subprocess acquires an exclusive flock on PID_FILE (it
          models the daemon's PID-lock), so even after the sleeper is killed
          the lock would linger briefly in a real race.
        - VERSION_FILE is populated with a stale SHA to force a version-mismatch
          restart path.

        After session-start kills the sleeper and tries to spawn the (fake) new
        daemon, the socket never appears → warning is emitted → exit 0.
        """
        _skip_if_unsupported()

        runtime_dir = session_env["runtime_dir"]
        pid_file = runtime_dir / "vaudeville.pid"
        version_file = runtime_dir / "vaudeville.version"

        # Start a sleeper that pretends to be the old daemon
        sleeper = subprocess.Popen(["sleep", "60"])
        try:
            pid_file.write_text(str(sleeper.pid))
            # Write a stale version so the script takes the restart branch
            version_file.write_text("stale-version-sha\n")

            # Hold an exclusive flock on PID_FILE from a separate process to
            # simulate the daemon's PID-lock surviving the kill window.
            flock_holder = subprocess.Popen(
                ["bash", "-c", f"flock -x {pid_file} sleep 60"]
            )
            try:
                result = _run_session_start(session_env["env"])
            finally:
                flock_holder.send_signal(__import__("signal").SIGTERM)
                flock_holder.wait()
        finally:
            sleeper.send_signal(__import__("signal").SIGTERM)
            sleeper.wait()

        assert result.returncode == 0, result.stderr.decode()
        stderr = result.stderr.decode()
        assert "daemon did not come up" in stderr, (
            f"Expected restart-race warning in stderr.\nstderr={stderr!r}"
        )
