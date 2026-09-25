"""Tests for hooks/runner.py — hook wire dispatch.

Note: `test_missing_key_allows` exercises passthrough over a real socket, not
the missing-key/fail-open guarantee. AC-13's real defence is proven in
`tests/test_hook_seam_contracts.py::test_ac13_*`.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(PROJECT_ROOT, "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import runner  # noqa: E402


def test_hook_request_shape() -> None:
    hook_input = {
        "hook_event_name": "Stop",
        "cwd": "/some/project",
        "session_id": "abc",
    }
    mock_client = MagicMock()
    mock_client.hook.return_value = {"stdout": "", "exit_code": 0}

    with (
        patch.object(sys, "argv", ["runner.py", "--harness", "claude-code"]),
        patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
        patch("runner.VaudevilleClient", return_value=mock_client),
        pytest.raises(SystemExit),
    ):
        runner._run()

    mock_client.hook.assert_called_once_with(
        {
            "op": "hook",
            "harness": "claude-code",
            "event": "Stop",
            "cwd": "/some/project",
            "payload": hook_input,
        }
    )


def test_stdout_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    hook_input = {"hook_event_name": "Stop", "cwd": "/p"}
    mock_client = MagicMock()
    mock_client.hook.return_value = {
        "stdout": '{"decision":"block"}',
        "exit_code": 2,
    }

    with (
        patch.object(sys, "argv", ["runner.py", "--harness", "claude-code"]),
        patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
        patch("runner.VaudevilleClient", return_value=mock_client),
        pytest.raises(SystemExit) as exc_info,
    ):
        runner._run()

    assert exc_info.value.code == 2
    assert capsys.readouterr().out.strip() == '{"decision":"block"}'


def test_no_yaml_no_pydantic() -> None:
    runner_path = os.path.join(HOOKS_DIR, "runner.py")
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('r', {runner_path!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print('yaml' in sys.modules, 'pydantic' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "False False", result.stderr


def test_daemon_allow_passthrough_over_socket(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Daemon-allow passthrough over a real socket; not an AC-13 proof."""
    with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
        sock_path = f.name
    pathlib.Path(sock_path).unlink()

    server_done = threading.Event()

    def _serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        srv.settimeout(3.0)
        conn, _ = srv.accept()
        data = b""
        while b"\n" not in data:
            data += conn.recv(4096)
        print("[fake-daemon] received hook request", file=sys.stderr)
        conn.sendall((json.dumps({"stdout": "{}", "exit_code": 0}) + "\n").encode())
        conn.close()
        srv.close()
        server_done.set()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(0.05)

    hook_input = {"hook_event_name": "Stop", "cwd": "/p"}
    try:
        with (
            patch.object(sys, "argv", ["runner.py", "--harness", "claude-code"]),
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("vaudeville.core.client.SOCKET_PATH", sock_path),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run()
        server_done.wait(timeout=3.0)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == "{}"
    finally:
        if pathlib.Path(sock_path).exists():
            pathlib.Path(sock_path).unlink()
