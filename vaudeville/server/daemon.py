"""Vaudeville inference daemon.

Loads model once, serves classify requests over Unix socket.
Hot-reloads rules on file change. Self-terminates after idle timeout.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import socket
import subprocess
import threading
import time
from ..core.protocol import parse_verdict
from ..core.rules import Rule, load_rules_layered, rules_search_path
from .inference import InferenceBackend

IDLE_TIMEOUT = 30 * 60  # 30 minutes
RULE_POLL_INTERVAL = 30  # seconds
RECV_CHUNK = 4096
MAX_REQUEST_SIZE = 1024 * 1024  # 1 MB

logger = logging.getLogger(__name__)


def _find_project_root() -> str | None:
    """Find the git working tree root, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def acquire_pid_lock(pid_file: str) -> int | None:
    """Acquire an exclusive flock on the PID file before loading the model.

    Returns the open fd on success (caller must keep it open), or None if
    another instance already holds the lock.
    """
    pid_fd = os.open(pid_file, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(pid_fd)
        return None
    os.ftruncate(pid_fd, 0)
    os.write(pid_fd, f"{os.getpid()}\n".encode())
    return pid_fd


def handle_request(
    data: bytes,
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> bytes:
    """Pure function: classify one request, return JSON response bytes."""
    try:
        request = json.loads(data.decode().strip())
        rule_name = request.get("rule", "")
        input_data = request.get("input", {})

        rule = rules.get(rule_name)
        if rule is None:
            logger.info(
                "CLASSIFY rule=%s verdict=clean action=block latency_ms=0 reason=unknown_rule",
                rule_name,
            )
            return _response("clean", f"Unknown rule: {rule_name}")

        text = str(input_data.get("text", ""))
        plugin_root = os.environ.get(
            "CLAUDE_PLUGIN_ROOT",
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
        context = rule.resolve_context(input_data, plugin_root)
        prompt = rule.format_prompt(text, context)
        t0 = time.monotonic()
        raw_output = backend.classify(prompt, max_tokens=50)
        latency_ms = (time.monotonic() - t0) * 1000
        response = parse_verdict(raw_output)
        safe_reason = response.reason.replace("\n", " ").replace("\r", " ")[:100]
        logger.info(
            "CLASSIFY rule=%s verdict=%s action=%s latency_ms=%.0f"
            " text_chars=%d prompt_chars=%d reason=%s",
            rule_name,
            response.verdict,
            rule.action,
            latency_ms,
            len(text),
            len(prompt),
            safe_reason,
        )
        return _response(response.verdict, response.reason, rule.action)

    except Exception as exc:
        logger.error("Request error: %s", exc)
        return _response("clean", "Inference error — fail open")


def _response(verdict: str, reason: str, action: str = "block") -> bytes:
    return (
        json.dumps(
            {
                "verdict": verdict,
                "reason": reason,
                "action": action,
            }
        ).encode()
        + b"\n"
    )


def _read_message(conn: socket.socket) -> bytes:
    """Read a newline-terminated message from a socket connection.

    Returns bytes up to and including the first newline.
    Returns empty bytes if the connection closes or the payload exceeds MAX_REQUEST_SIZE.
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


def _scan_dir_mtime(rules_dir: str) -> float:
    """Return the max mtime of YAML files in a single rules directory."""
    max_mtime = 0.0
    try:
        for f in os.listdir(rules_dir):
            if f.endswith(".yaml") or f.endswith(".yml"):
                mtime = os.path.getmtime(os.path.join(rules_dir, f))
                max_mtime = max(max_mtime, mtime)
    except OSError:
        pass
    return max_mtime


class VaudevilleDaemon:
    def __init__(
        self,
        socket_path: str,
        pid_file: str,
        plugin_root: str,
        backend: InferenceBackend,
        pid_fd: int | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._pid_file = pid_file
        self._plugin_root = plugin_root
        self._backend = backend
        self._project_root = _find_project_root()
        self._rules: dict[str, Rule] = {}
        self._rules_lock = threading.Lock()
        self._last_request = time.monotonic()
        self._stop_event = threading.Event()
        self._pid_fd: int | None = pid_fd

    def serve(self) -> None:
        """Load rules, write PID, bind socket, serve until idle timeout."""
        # Acquire PID lock if not pre-acquired by __main__
        if self._pid_fd is None:
            pid_fd = acquire_pid_lock(self._pid_file)
            if pid_fd is None:
                logger.info("Another instance holds PID lock — exiting")
                return
            self._pid_fd = pid_fd

        with self._rules_lock:
            self._rules = load_rules_layered(self._plugin_root, self._project_root)
        logger.info("Loaded %d rules", len(self._rules))

        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        server_socket.bind(self._socket_path)
        server_socket.listen(16)
        server_socket.settimeout(1.0)

        threading.Thread(target=self._watch_rules, daemon=True).start()
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
            data = _read_message(conn)
            with self._rules_lock:
                current_rules = dict(self._rules)

            response = handle_request(data, current_rules, self._backend)
            conn.sendall(response)
            self._last_request = time.monotonic()
        except Exception as exc:
            logger.error("Client handler error: %s", exc)
        finally:
            conn.close()

    def _watch_rules(self) -> None:
        last_mtime = self._rules_mtime()
        while not self._stop_event.is_set():
            time.sleep(RULE_POLL_INTERVAL)
            current = self._rules_mtime()
            if current != last_mtime:
                new_rules = load_rules_layered(
                    self._plugin_root,
                    self._project_root,
                )
                with self._rules_lock:
                    self._rules = new_rules
                last_mtime = current
                logger.info("Rules reloaded (%d rules)", len(new_rules))

    def _rules_mtime(self) -> float:
        return max(
            (
                _scan_dir_mtime(d)
                for d in rules_search_path(
                    self._plugin_root,
                    self._project_root,
                )
            ),
            default=0.0,
        )

    def _cleanup(self) -> None:
        if self._pid_fd is not None:
            try:
                os.close(self._pid_fd)
            except OSError:
                pass
            self._pid_fd = None
        for path in (self._socket_path, self._pid_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
