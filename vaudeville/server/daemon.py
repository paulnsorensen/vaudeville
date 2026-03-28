"""Vaudeville inference daemon.

Loads model once, serves classify requests over Unix socket.
Hot-reloads rules on file change. Self-terminates after idle timeout.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

from ..core.protocol import parse_verdict
from ..core.rules import Rule, load_rules
from .inference import InferenceBackend

IDLE_TIMEOUT = 30 * 60   # 30 minutes
RULE_POLL_INTERVAL = 30  # seconds
RECV_CHUNK = 4096

logger = logging.getLogger(__name__)


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
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
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
    return json.dumps({
        "verdict": verdict, "reason": reason, "action": action,
    }).encode() + b"\n"


class VaudevilleDaemon:
    def __init__(
        self,
        socket_path: str,
        pid_file: str,
        rules_dir: str,
        backend: InferenceBackend,
    ) -> None:
        self._socket_path = socket_path
        self._pid_file = pid_file
        self._rules_dir = rules_dir
        self._backend = backend
        self._rules: dict[str, Rule] = {}
        self._rules_lock = threading.Lock()
        self._last_request = time.monotonic()
        self._stop_event = threading.Event()

    def serve(self) -> None:
        """Load rules, write PID, bind socket, serve until idle timeout."""
        with self._rules_lock:
            self._rules = load_rules(self._rules_dir)
        logger.info("[vaudeville] Loaded %d rules", len(self._rules))

        Path(self._pid_file).write_text(str(os.getpid()))

        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        server_socket.bind(self._socket_path)
        server_socket.listen(16)
        server_socket.settimeout(1.0)

        threading.Thread(target=self._watch_rules, daemon=True).start()
        logger.info("[vaudeville] Listening on %s", self._socket_path)

        try:
            self._accept_loop(server_socket)
        finally:
            server_socket.close()
            self._cleanup()

    def _accept_loop(self, server_socket: socket.socket) -> None:
        while not self._stop_event.is_set():
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
            data = b""
            while True:
                chunk = conn.recv(RECV_CHUNK)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            with self._rules_lock:
                current_rules = dict(self._rules)

            response = handle_request(data, current_rules, self._backend)
            conn.sendall(response)
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
                new_rules = load_rules(self._rules_dir)
                with self._rules_lock:
                    self._rules = new_rules
                last_mtime = current
                logger.info("[vaudeville] Rules reloaded (%d rules)", len(new_rules))

    def _rules_mtime(self) -> float:
        try:
            mtimes = [
                os.path.getmtime(os.path.join(self._rules_dir, f))
                for f in os.listdir(self._rules_dir)
                if f.endswith(".yaml") or f.endswith(".yml")
            ]
            return max(mtimes) if mtimes else 0.0
        except OSError:
            return 0.0

    def _cleanup(self) -> None:
        for path in (self._socket_path, self._pid_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
