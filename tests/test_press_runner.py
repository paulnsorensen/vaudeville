"""Adversarial attack on hooks/runner.py `main()` (AC-3, AC-4, AC-15).

The runner must never raise an uncaught exception and must always exit
with an allow response when stdin, the daemon reply, or the harness
argument is malformed.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNNER_PATH = os.path.join(_PROJECT_ROOT, "hooks", "runner.py")


def _load_runner_module() -> Any:
    """Import hooks/runner.py as an isolated module (not via sys.path)."""
    spec = importlib.util.spec_from_file_location("press_runner", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner_module()

HOOK_INPUT = json.dumps({"hook_event_name": "Stop", "cwd": "/p"})


def _run_main_and_capture(
    argv: list[str], stdin_text: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    with (
        patch.object(sys, "argv", argv),
        patch("sys.stdin", io.StringIO(stdin_text)),
        pytest.raises(SystemExit) as exc_info,
    ):
        runner.main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code, capsys.readouterr().out.strip()


class _FakeDaemon:
    """A one-shot Unix-socket daemon that replies with a fixed JSON line."""

    def __init__(self, sock_path: str, reply: object) -> None:
        self._sock_path = sock_path
        self._reply = reply
        self.done = threading.Event()

    def __enter__(self) -> "_FakeDaemon":
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.done.wait(timeout=3.0)

    def _serve(self) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self._sock_path)
        srv.listen(1)
        srv.settimeout(3.0)
        try:
            conn, _ = srv.accept()
            data = b""
            while b"\n" not in data:
                data += conn.recv(4096)
            conn.sendall((json.dumps(self._reply) + "\n").encode())
            conn.close()
        finally:
            srv.close()
            self.done.set()


@pytest.fixture
def sock_path(short_sock_path: str) -> Iterator[str]:
    yield os.path.join(short_sock_path, "vaudeville.sock")


class TestMalformedStdin:
    def test_utf8_bom_prefixed_stdin_fails_open(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bom_text = "﻿" + HOOK_INPUT
        code, out = _run_main_and_capture(
            ["runner.py", "--harness", "claude-code"], bom_text, capsys
        )
        assert code == 0
        assert out == ""

    def test_over_one_megabyte_payload_does_not_crash(
        self, sock_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        huge = "x" * (1_200_000)
        payload = json.dumps(
            {"hook_event_name": "Stop", "cwd": "/p", "big_field": huge}
        )
        reply = {"stdout": "{}", "exit_code": 0}
        with (
            _FakeDaemon(sock_path, reply),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
        ):
            code, out = _run_main_and_capture(
                ["runner.py", "--harness", "claude-code"], payload, capsys
            )
        assert 0 <= code <= 255
        assert out == "{}"


class TestMalformedDaemonReply:
    def test_exit_code_as_string_fails_open(
        self, sock_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reply = {"stdout": "{}", "exit_code": "0"}
        with (
            _FakeDaemon(sock_path, reply),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
        ):
            code, out = _run_main_and_capture(
                ["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys
            )
        # is_hook_response rejects a non-int exit_code, so client.hook()
        # returns None and the runner falls open.
        assert code == 0
        assert out == ""

    def test_exit_code_999_does_not_raise(
        self, sock_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reply = {"stdout": "{}", "exit_code": 999}
        with (
            _FakeDaemon(sock_path, reply),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
        ):
            code, out = _run_main_and_capture(
                ["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys
            )
        # is_hook_response only checks isinstance(int) — no range check —
        # so an out-of-band exit_code passes through to sys.exit() as-is.
        # This never raises; the OS truncates the real process exit status.
        assert code == 999
        assert out == "{}"

    def test_exit_code_negative_one_does_not_raise(
        self, sock_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reply = {"stdout": "{}", "exit_code": -1}
        with (
            _FakeDaemon(sock_path, reply),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
        ):
            code, out = _run_main_and_capture(
                ["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys
            )
        assert code == -1
        assert out == "{}"

    def test_stdout_non_string_fails_open(
        self, sock_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reply = {"stdout": {"nested": "object"}, "exit_code": 0}
        with (
            _FakeDaemon(sock_path, reply),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
        ):
            code, out = _run_main_and_capture(
                ["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys
            )
        assert code == 0
        assert out == ""

    def test_extra_unknown_keys_are_ignored_not_fatal(
        self, sock_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reply = {
            "stdout": "{}",
            "exit_code": 0,
            "unexpected_extra_field": "should be ignored",
        }
        with (
            _FakeDaemon(sock_path, reply),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
        ):
            code, out = _run_main_and_capture(
                ["runner.py", "--harness", "claude-code"], HOOK_INPUT, capsys
            )
        assert code == 0
        assert out == "{}"


class TestHarnessArgument:
    def test_traversal_style_harness_falls_open_as_unknown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_main_and_capture(
            ["runner.py", "--harness", "../claude-code"], HOOK_INPUT, capsys
        )
        assert code == 0
        assert out == ""

    def test_unknown_extra_argument_falls_open_instead_of_exit_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_main_and_capture(
            ["runner.py", "--event", "Stop"], HOOK_INPUT, capsys
        )
        assert code == 0
        assert out == ""

    def test_bare_harness_flag_without_value_falls_open(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_main_and_capture(
            ["runner.py", "--harness"], HOOK_INPUT, capsys
        )
        assert code == 0
        assert out == ""
