"""`handle_request` routes `op: hook` through the real daemon socket to
`vaudeville.server.hook.handle_hook_request`, and fails open otherwise."""

from __future__ import annotations

import functools
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.server.agents import decide
from vaudeville.server.daemon import DaemonConfig, VaudevilleDaemon
from vaudeville.server.hook import pipeline as pipeline_module


def _write_rule(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".vaudeville" / "rules"
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


def _patch_decide(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart(output)])

    fn = functools.partial(decide, model_override=FunctionModel(respond))
    monkeypatch.setattr(pipeline_module, "default_decide", fn)


def _start_daemon() -> tuple[VaudevilleDaemon, str]:
    with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
        socket_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".pid", delete=False) as f:
        pid_file = f.name
    with tempfile.NamedTemporaryFile(suffix=".version", delete=True) as f:
        version_file = f.name
    Path(socket_path).unlink()

    daemon = VaudevilleDaemon(DaemonConfig(socket_path, pid_file, os.getcwd(), version_file))
    thread = threading.Thread(target=daemon.serve, daemon=True)
    thread.start()

    for _ in range(30):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.1)
                probe.connect(socket_path)
                break
        except (ConnectionRefusedError, FileNotFoundError):
            time.sleep(0.05)
    else:
        raise RuntimeError("Daemon socket not ready")
    return daemon, socket_path


def _send(socket_path: str, request: dict[str, object]) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3.0)
        sock.connect(socket_path)
        sock.sendall(json.dumps(request).encode() + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return dict(json.loads(data.decode().strip()))


class TestDaemonHookRouting:
    def test_hook_op_routes_to_handle_hook_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path)
        _patch_decide(monkeypatch, "safe")
        daemon, socket_path = _start_daemon()
        try:
            response = _send(
                socket_path,
                {
                    "op": "hook",
                    "harness": "claude-code",
                    "event": "PreToolUse",
                    "cwd": str(tmp_path),
                    "payload": {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Write",
                        "tool_input": {"content": "hello"},
                        "cwd": str(tmp_path),
                    },
                },
            )
            assert response["exit_code"] == 0
        finally:
            daemon._stop_event.set()

    def test_unknown_op_allows(self) -> None:
        daemon, socket_path = _start_daemon()
        try:
            response = _send(socket_path, {"op": "bogus"})
            assert response == {"stdout": "", "exit_code": 0}
        finally:
            daemon._stop_event.set()

    def test_handler_error_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(request: object, *, event_logger: object = None) -> object:
            del request, event_logger
            raise RuntimeError("boom")

        from vaudeville.server import _handlers

        monkeypatch.setattr(_handlers, "handle_hook_request", _boom)
        daemon, socket_path = _start_daemon()
        try:
            response = _send(socket_path, {"op": "hook"})
            assert response == {"stdout": "", "exit_code": 0}
        finally:
            daemon._stop_event.set()
