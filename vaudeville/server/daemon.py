"""Vaudeville inference daemon.

Loads model once, serves classify requests over Unix socket.
Hot-reloads rules on file change. Self-terminates after idle timeout.
"""

from __future__ import annotations

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
from ..core.protocol import parse_verdict
from ..core.rules import Rule, load_rules_layered, rules_search_path
from .inference import InferenceBackend

IDLE_TIMEOUT = 60 * 60  # 60 minutes
RULE_POLL_INTERVAL = 30  # seconds
RECV_CHUNK = 4096
MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1 MB
CLIENT_TIMEOUT = 10.0  # seconds
THREAD_WARN = 20
THREAD_KILL = 50

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
        raw_output = backend.classify(prompt, max_tokens=50)
        response = parse_verdict(raw_output)
        return _response(response.verdict, response.reason, rule.action)

    except Exception as exc:
        logger.error("[vaudeville] Request error: %s", exc)
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
            logger.warning(
                "[vaudeville] Request exceeded %d bytes — dropping", MAX_REQUEST_SIZE
            )
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
        version_file: str = VERSION_FILE,
    ) -> None:
        self._socket_path = socket_path
        self._pid_file = pid_file
        self._plugin_root = plugin_root
        self._backend = backend
        self._version_file = version_file
        self._project_root = _find_project_root()
        self._rules: dict[str, Rule] = {}
        self._rules_lock = threading.Lock()
        self._backend_lock = threading.Lock()
        self._last_request = time.monotonic()
        self._stop_event = threading.Event()
        self._reload_event = threading.Event()
        self._pid_fd: int | None = None

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, self._handle_reload)

    def _handle_signal(self, _signum: int, _frame: object) -> None:
        self._stop_event.set()

    def _handle_reload(self, _signum: int, _frame: object) -> None:
        self._reload_event.set()

    def serve(self) -> None:
        """Load rules, write PID, bind socket, serve until idle timeout."""
        ensure_runtime_dir()
        if threading.current_thread() is threading.main_thread():
            self._install_signal_handlers()

        with self._rules_lock:
            self._rules = load_rules_layered(self._plugin_root, self._project_root)
        logger.info("[vaudeville] Loaded %d rules", len(self._rules))

        pid_fd = os.open(self._pid_file, os.O_WRONLY | os.O_CREAT, 0o644)
        try:
            fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(pid_fd)
            logger.info("[vaudeville] Another instance holds PID lock — exiting")
            return
        os.ftruncate(pid_fd, 0)
        os.write(pid_fd, f"{os.getpid()}\n".encode())
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

        threading.Thread(target=self._watch_rules, daemon=True).start()
        threading.Thread(target=self._watch_threads, daemon=True).start()
        logger.info("[vaudeville] Listening on %s", self._socket_path)

        try:
            self._accept_loop(server_socket)
        finally:
            server_socket.close()
            self._cleanup()

    def _accept_loop(self, server_socket: socket.socket) -> None:
        while not self._stop_event.is_set():
            if self._reload_event.is_set():
                self._reload_event.clear()
                new_rules = load_rules_layered(self._plugin_root, self._project_root)
                with self._rules_lock:
                    self._rules = new_rules
                logger.info(
                    "[vaudeville] Rules reloaded via SIGHUP (%d rules)", len(new_rules)
                )
            idle = time.monotonic() - self._last_request
            if idle > IDLE_TIMEOUT:
                logger.info("[vaudeville] Idle timeout — shutting down")
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

            with self._rules_lock:
                current_rules = dict(self._rules)

            with self._backend_lock:
                response = handle_request(bytes(data), current_rules, self._backend)
            conn.sendall(response)
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("[vaudeville] Request handled in %.1fms", elapsed_ms)
            self._last_request = time.monotonic()
        except Exception as exc:
            logger.error("[vaudeville] Client handler error: %s", exc)
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
                logger.info("[vaudeville] Rules reloaded (%d rules)", len(new_rules))

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

    def _watch_threads(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(10)
            count = threading.active_count()
            if count > THREAD_KILL:
                logger.error(
                    "[vaudeville] Thread count %d exceeds kill threshold — shutting down",
                    count,
                )
                self._stop_event.set()
            elif count > THREAD_WARN:
                logger.warning(
                    "[vaudeville] Thread count %d exceeds warning threshold", count
                )

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
            logger.warning("[vaudeville] Could not write version stamp")

    def _cleanup(self) -> None:
        if self._pid_fd is not None:
            os.close(self._pid_fd)
            self._pid_fd = None
        for path in (self._socket_path, self._pid_file, self._version_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
