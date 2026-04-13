"""Vaudeville inference daemon.

Loads model once, serves classify requests over Unix socket.
Self-terminates after idle timeout.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time

from ..core.paths import VERSION_FILE, ensure_runtime_dir
from ..core.protocol import ClassifyResult, compute_confidence, parse_verdict
from .event_log import EventLogger
from .inference import (
    CachedBackend,
    CachedLogprobBackend,
    InferenceBackend,
    LogprobBackend,
)

IDLE_TIMEOUT = 60 * 60  # 60 minutes
RECV_CHUNK = 4096
MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1 MB
CLIENT_TIMEOUT = 10.0  # seconds
THREAD_WARN = 20
THREAD_KILL = 50

logger = logging.getLogger(__name__)


def acquire_pid_lock(pid_file: str) -> int | None:
    """Acquire an exclusive flock on the PID file before loading the model.

    Returns the open fd on success (caller must keep it open), or None if
    another instance already holds the lock or the PID file cannot be prepared.
    """
    pid_fd: int | None = None
    try:
        pid_fd = os.open(pid_file, os.O_WRONLY | os.O_CREAT, 0o644)
        fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(pid_fd, 0)
        os.write(pid_fd, f"{os.getpid()}\n".encode())
        return pid_fd
    except OSError as exc:
        if pid_fd is not None:
            try:
                os.close(pid_fd)
            except OSError:
                pass
        if exc.errno not in (errno.EWOULDBLOCK, errno.EACCES):
            logger.error("Failed to acquire PID lock file %r: %s", pid_file, exc)
        return None


def _run_inference(
    backend: InferenceBackend,
    prompt: str,
    prefix_len: int = 0,
) -> ClassifyResult:
    """Run inference with optional prefix caching and logprobs."""
    if prefix_len > 0 and isinstance(backend, CachedLogprobBackend):
        return backend.classify_cached_with_logprobs(prompt, prefix_len)
    elif prefix_len > 0 and isinstance(backend, CachedBackend):
        text = backend.classify_cached(prompt, prefix_len)
        return ClassifyResult(text=text)
    elif prefix_len > 0:
        logger.debug(
            "prefix_len=%d but backend lacks cached methods — uncached", prefix_len
        )
    if isinstance(backend, LogprobBackend):
        return backend.classify_with_logprobs(prompt, max_tokens=50)
    text = backend.classify(prompt, max_tokens=50)
    return ClassifyResult(text=text)


def handle_request(
    data: bytes,
    backend: InferenceBackend,
    event_logger: EventLogger | None = None,
) -> bytes:
    """Pure function: classify one request, return JSON response bytes."""
    try:
        request = json.loads(data.decode().strip())
        prompt = request.get("prompt", "")
        rule = request.get("rule", "")
        prefix_len = request.get("prefix_len", 0)

        logger.debug("prompt=%d chars prefix_len=%d", len(prompt), prefix_len)
        t0 = time.monotonic()
        result = _run_inference(backend, prompt, prefix_len)
        elapsed_ms = (time.monotonic() - t0) * 1000
        response = parse_verdict(result.text)
        confidence = compute_confidence(result.logprobs, response.verdict)
        safe_reason = response.reason.replace("\n", " ").replace("\r", " ")[:100]
        logger.info(
            "CLASSIFY verdict=%s confidence=%.3f "
            " latency_ms=%.0f prompt_chars=%d reason=%s",
            response.verdict,
            confidence,
            elapsed_ms,
            len(prompt),
            safe_reason,
        )

        if event_logger is not None:
            from .event_log import ClassificationEvent

            event_logger.log_event(
                ClassificationEvent(
                    rule=rule,
                    verdict=response.verdict,
                    confidence=confidence,
                    latency_ms=elapsed_ms,
                    prompt_chars=len(prompt),
                    reason=response.reason,
                    input_snippet=prompt[:500],
                )
            )

        return _response(response.verdict, response.reason, confidence)

    except Exception as exc:
        logger.error("Request error: %s", exc)
        return _response("clean", "Inference error — fail open")


def _response(verdict: str, reason: str, confidence: float = 1.0) -> bytes:
    return (
        json.dumps(
            {
                "verdict": verdict,
                "reason": reason,
                "confidence": confidence,
            }
        ).encode()
        + b"\n"
    )


def _read_message(conn: socket.socket) -> bytes:
    """Read a newline-terminated message from a socket connection.

    Returns the bytes up to (but not including) the first newline.
    If the connection closes before a newline is received, returns all buffered
    bytes read so far (which may be empty). Returns empty bytes if the payload
    exceeds MAX_REQUEST_SIZE.
    """
    buf = bytearray()
    while True:
        chunk = conn.recv(RECV_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in buf:
            return bytes(buf.split(b"\n", 1)[0])
        if len(buf) > MAX_REQUEST_SIZE:
            logger.warning("Request exceeded %d bytes — dropping", MAX_REQUEST_SIZE)
            return b""
    return bytes(buf)


class VaudevilleDaemon:
    def __init__(
        self,
        socket_path: str,
        pid_file: str,
        plugin_root: str,
        backend: InferenceBackend,
        version_file: str = VERSION_FILE,
        pid_fd: int | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._pid_file = pid_file
        self._plugin_root = plugin_root
        self._backend = backend
        self._version_file = version_file
        self._backend_lock = threading.Lock()
        self._last_request = time.monotonic()
        self._stop_event = threading.Event()
        self._pid_fd: int | None = pid_fd
        self._event_logger = event_logger

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    def _handle_signal(self, _signum: int, _frame: object) -> None:
        self._stop_event.set()

    def serve(self) -> None:
        """Write PID, bind socket, serve until idle timeout."""
        ensure_runtime_dir()
        if threading.current_thread() is threading.main_thread():
            self._install_signal_handlers()

        # Acquire PID lock if not pre-acquired by __main__
        if self._pid_fd is None:
            pid_fd = acquire_pid_lock(self._pid_file)
            if pid_fd is None:
                logger.info("Another instance holds PID lock — exiting")
                return
            self._pid_fd = pid_fd

        self._write_version_stamp()

        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(self._socket_path)
        except (FileNotFoundError, PermissionError):
            pass
        server_socket.bind(self._socket_path)
        server_socket.listen(16)
        server_socket.settimeout(1.0)

        threading.Thread(target=self._watch_threads, daemon=True).start()
        logger.info("Listening on %s", self._socket_path)

        try:
            self._accept_loop(server_socket)
        finally:
            server_socket.close()
            self._cleanup()

    def _accept_loop(self, server_socket: socket.socket) -> None:
        while not self._stop_event.is_set():
            idle = time.monotonic() - self._last_request
            if idle > IDLE_TIMEOUT:
                logger.info("Idle timeout — shutting down")
                break
            try:
                conn, _ = server_socket.accept()
                threading.Thread(
                    target=self._handle_client, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(CLIENT_TIMEOUT)
            t0 = time.monotonic()
            data = _read_message(conn)

            with self._backend_lock:
                response = handle_request(
                    bytes(data), self._backend, self._event_logger
                )
            conn.sendall(response)
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("Request handled in %.1fms", elapsed_ms)
            self._last_request = time.monotonic()
        except Exception as exc:
            logger.error("Client handler error: %s", exc)
        finally:
            conn.close()

    def _watch_threads(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(10)
            count = threading.active_count()
            if count > THREAD_KILL:
                logger.error(
                    "Thread count %d exceeds kill threshold — shutting down",
                    count,
                )
                self._stop_event.set()
            elif count > THREAD_WARN:
                logger.warning("Thread count %d exceeds warning threshold", count)

    def _write_version_stamp(self) -> None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self._plugin_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            stamp = result.stdout.strip() if result.returncode == 0 else "unknown"
        except (OSError, subprocess.TimeoutExpired):
            stamp = "unknown"
        try:
            with open(self._version_file, "w") as f:
                f.write(stamp + "\n")
        except OSError:
            logger.warning("Could not write version stamp")

    def _cleanup(self) -> None:
        if self._event_logger is not None:
            self._event_logger.close()
        if self._pid_fd is not None:
            os.close(self._pid_fd)
            self._pid_fd = None
        for path in (self._socket_path, self._pid_file, self._version_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
