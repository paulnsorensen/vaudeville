"""Tests for daemon request handling and verdict parsing."""

from __future__ import annotations

import json
import socket
import threading
import time

from vaudeville.server.daemon import handle_request, VaudevilleDaemon
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


class TestDaemonSocketProtocol:
    def test_daemon_serves_request_via_socket(self) -> None:
        import tempfile

        # Unix sockets have a 104-char path limit on macOS — use /tmp directly
        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            socket_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", dir="/tmp", delete=False) as f:
            pid_file = f.name
        import os

        os.unlink(socket_path)  # daemon will re-create it
        backend = MockBackend(verdict="clean", reason="socket test")

        import os
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        daemon = VaudevilleDaemon(socket_path, pid_file, plugin_root, backend)

        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        time.sleep(0.2)  # Brief pause for socket to bind

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3.0)
            sock.connect(socket_path)
            payload = (
                json.dumps(
                    {
                        "rule": "violation-detector",
                        "input": {"text": "All good."},
                    }
                ).encode()
                + b"\n"
            )
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
