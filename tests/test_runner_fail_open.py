"""Tests for hooks/runner.py — fail-open paths."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import time
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(PROJECT_ROOT, "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import runner  # noqa: E402

HOOK_INPUT = json.dumps({"hook_event_name": "Stop", "cwd": "/p"})


def _run_and_capture(
    argv: list[str], stdin_text: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    with (
        patch.object(sys, "argv", argv),
        patch("sys.stdin", io.StringIO(stdin_text)),
        pytest.raises(SystemExit) as exc_info,
    ):
        runner._run()
    return exc_info.value.code, capsys.readouterr().out.strip()  # type: ignore[return-value]


def test_unreachable(short_sock_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    sock_path = os.path.join(short_sock_path, "nonexistent.sock")
    with patch("vaudeville.core.client.SOCKET_PATH", sock_path):
        code, out = _run_and_capture(["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys)
    assert code == 0
    assert out == ""


def test_timeout(short_sock_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    sock_path = os.path.join(short_sock_path, "timeout.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def _accept_and_stall() -> None:
        conn, _ = srv.accept()
        time.sleep(1.0)
        conn.close()

    t = threading.Thread(target=_accept_and_stall, daemon=True)
    t.start()

    try:
        with (
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
            patch("vaudeville.core.client.READ_TIMEOUT", 0.2),
        ):
            code, out = _run_and_capture(
                ["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys
            )
        assert code == 0
        assert out == ""
    finally:
        srv.close()


def test_daemon_error(short_sock_path: str, capsys: pytest.CaptureFixture[str]) -> None:
    sock_path = os.path.join(short_sock_path, "error.sock")
    server_done = threading.Event()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(3.0)

    def _serve() -> None:
        conn, _ = srv.accept()
        data = b""
        while b"\n" not in data:
            data += conn.recv(4096)
        conn.sendall((json.dumps({"error": "boom"}) + "\n").encode())
        conn.close()
        srv.close()
        server_done.set()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    with patch("vaudeville.core.client.SOCKET_PATH", sock_path):
        code, out = _run_and_capture(["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys)
    server_done.wait(timeout=3.0)
    assert server_done.is_set()
    assert code == 0
    assert out == ""


def test_unknown_harness(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run_and_capture(["runner.py", "--harness", "bogus-cli"], HOOK_INPUT, capsys)
    assert code == 0
    assert out == ""
