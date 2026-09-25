"""Tests for daemon socket lifecycle, PID locking, signals, and version stamping."""

from __future__ import annotations

import json
import os
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path

from vaudeville.server import DaemonConfig, VaudevilleDaemon
from vaudeville.server.event_log import EventLogger
from vaudeville.server.log_config import LogConfig


def _wait_for_socket(socket_path: str, attempts: int = 20) -> None:
    for _ in range(attempts):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.1)
                probe.connect(socket_path)
                return
        except (ConnectionRefusedError, FileNotFoundError):
            time.sleep(0.05)
    raise RuntimeError("Daemon socket not ready")


class TestDaemonEventLoggerWiring:
    def test_daemon_passes_event_logger(self, tmp_path: Path) -> None:
        """VaudevilleDaemon stores the event_logger it is constructed with."""
        logs_dir = str(tmp_path / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(
            DaemonConfig("/tmp/test.sock", "/tmp/test.pid", plugin_root),
            event_logger=el,
        )
        assert daemon._event_logger is el
        el.close()

    def test_daemon_cleanup_closes_logger(self, tmp_path: Path) -> None:
        """_cleanup() closes the event logger sinks."""
        logs_dir = str(tmp_path / "logs")
        el = EventLogger(config=LogConfig(), logs_dir=logs_dir)
        assert el._events_id is not None

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sock = str(tmp_path / "test.sock")
        pid = str(tmp_path / "test.pid")
        Path(pid).touch()
        daemon = VaudevilleDaemon(
            DaemonConfig(sock, pid, plugin_root),
            event_logger=el,
        )
        daemon._cleanup()
        assert el._events_id is None
        assert el._violations_id is None

    def test_daemon_none_event_logger_default(self) -> None:
        """Daemon defaults to None event_logger."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(DaemonConfig("/tmp/test.sock", "/tmp/test.pid", plugin_root))
        assert daemon._event_logger is None


class TestDaemonSocketProtocol:
    def test_oversized_payload_returns_fail_open(self) -> None:
        """Daemon drops payloads exceeding MAX_REQUEST_SIZE and fails open."""
        from vaudeville.server.daemon import MAX_REQUEST_SIZE

        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            pid_file = f.name
        Path(socket_path).unlink()

        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.NamedTemporaryFile(suffix=".version", delete=True) as vf:
            version_file = vf.name
        daemon = VaudevilleDaemon(DaemonConfig(socket_path, pid_file, plugin_root, version_file))

        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        _wait_for_socket(socket_path)

        try:
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
            assert response == {"stdout": "", "exit_code": 0}
        finally:
            daemon._stop_event.set()


class TestSignalHandlers:
    def test_sigterm_sets_stop_event(self) -> None:
        """Verify SIGTERM triggers graceful shutdown via _stop_event."""
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(DaemonConfig("/tmp/test.sock", "/tmp/test.pid", plugin_root))

        # Install handlers (normally done by serve())
        daemon._install_signal_handlers()
        assert not daemon._stop_event.is_set()

        # Send SIGTERM to ourselves
        os.kill(os.getpid(), signal.SIGTERM)
        assert daemon._stop_event.is_set()


class TestVersionStamp:
    def _make_daemon(self, socket_path: str, pid_file: str, version_file: str) -> VaudevilleDaemon:
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return VaudevilleDaemon(DaemonConfig(socket_path, pid_file, plugin_root, version_file))

    def test_version_file_written_after_serve(self) -> None:
        """serve() writes the version file once the PID lock is acquired."""
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            pid_file = f.name
        with tempfile.NamedTemporaryFile(suffix=".version", delete=False) as f:
            version_file = f.name
        Path(socket_path).unlink()

        daemon = self._make_daemon(socket_path, pid_file, version_file)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        try:
            _wait_for_socket(socket_path, attempts=40)
        except RuntimeError:
            daemon._stop_event.set()
            raise

        try:
            assert Path(version_file).exists(), "version file not written"
            content = Path(version_file).open().read().strip()
            assert content != "", "version file is empty"
        finally:
            daemon._stop_event.set()
            thread.join(timeout=3)

    def test_version_file_cleaned_on_shutdown(self) -> None:
        """_cleanup() removes the version file after the daemon stops."""
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
            pid_file = f.name
        with tempfile.NamedTemporaryFile(suffix=".version", delete=False) as f:
            version_file = f.name
        Path(socket_path).unlink()

        daemon = self._make_daemon(socket_path, pid_file, version_file)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()

        try:
            _wait_for_socket(socket_path, attempts=40)
        except RuntimeError:
            pass

        daemon._stop_event.set()
        thread.join(timeout=5)

        assert not Path(version_file).exists(), "version file not removed on cleanup"


class TestAcquirePidLock:
    def test_acquires_and_writes_pid(self, tmp_path: Path) -> None:
        from vaudeville.server.daemon import acquire_pid_lock

        pid_file = str(tmp_path / "test.pid")
        fd = acquire_pid_lock(pid_file)
        assert fd is not None
        content = Path(pid_file).read_text()
        assert str(os.getpid()) in content
        os.close(fd)

    def test_second_caller_gets_none(self, tmp_path: Path) -> None:
        from vaudeville.server.daemon import acquire_pid_lock

        pid_file = str(tmp_path / "test.pid")
        fd1 = acquire_pid_lock(pid_file)
        assert fd1 is not None
        fd2 = acquire_pid_lock(pid_file)
        assert fd2 is None
        os.close(fd1)

    def test_lock_released_after_close(self, tmp_path: Path) -> None:
        from vaudeville.server.daemon import acquire_pid_lock

        pid_file = str(tmp_path / "test.pid")
        fd1 = acquire_pid_lock(pid_file)
        assert fd1 is not None
        os.close(fd1)
        fd2 = acquire_pid_lock(pid_file)
        assert fd2 is not None
        os.close(fd2)
