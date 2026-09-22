"""Tests for the fire-and-forget named-command runner (AC-11)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from vaudeville.server.effects import run_named_command
from vaudeville.server.user_config import ProviderConfig, UserConfig


class TestRunStartsWithoutShell:
    def test_popen_called_with_list_and_no_shell(self) -> None:
        config = UserConfig(commands={"notify": ["/bin/echo", "hi"]})
        fake_process = MagicMock()
        fake_process.stdin = MagicMock()

        with patch("subprocess.Popen", return_value=fake_process) as popen:
            run_named_command("notify", config, "{}", timeout=1.0)

        args, kwargs = popen.call_args
        assert isinstance(args[0], list)
        assert args[0] == ["/bin/echo", "hi"]
        assert kwargs.get("shell") in (None, False)

    def test_event_json_written_to_stdin(self) -> None:
        config = UserConfig(commands={"notify": ["/bin/echo", "hi"]})
        fake_process = MagicMock()
        fake_process.stdin = MagicMock()

        with patch("subprocess.Popen", return_value=fake_process):
            run_named_command("notify", config, '{"event": "Stop"}', timeout=1.0)

        deadline = time.monotonic() + 2.0
        while not fake_process.stdin.close.called and time.monotonic() < deadline:
            time.sleep(0.01)

        fake_process.stdin.write.assert_called_once_with(b'{"event": "Stop"}')
        fake_process.stdin.close.assert_called_once()


class TestRunDoesNotWait:
    def test_returns_before_child_exits(self) -> None:
        config = UserConfig(
            commands={"sleeper": [sys.executable, "-c", "import time; time.sleep(2)"]}
        )

        started = time.monotonic()
        result = run_named_command("sleeper", config, "{}", timeout=5.0)
        elapsed = time.monotonic() - started

        assert result is True
        assert elapsed < 1.0


class TestRunEnforcesTimeout:
    def test_process_killed_after_timeout_expires(self) -> None:
        fake_process = MagicMock()
        fake_process.stdin = MagicMock()
        fake_process.wait.side_effect = subprocess.TimeoutExpired(
            cmd="sleeper", timeout=0.05
        )
        config = UserConfig(commands={"sleeper": ["/bin/sleep", "5"]})

        with patch("subprocess.Popen", return_value=fake_process):
            run_named_command("sleeper", config, "{}", timeout=0.05)
            deadline = time.monotonic() + 2.0
            while not fake_process.kill.called and time.monotonic() < deadline:
                time.sleep(0.01)

        fake_process.kill.assert_called_once()


class TestRunUndefinedNameSkipped:
    def test_undefined_name_skipped_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = UserConfig(commands={})

        with caplog.at_level(
            logging.WARNING, logger="vaudeville.server.effects.run_command"
        ):
            result = run_named_command("missing", config, "{}", timeout=1.0)

        assert result is False
        assert any("missing" in record.getMessage() for record in caplog.records)


class TestRunStdinNeverBlocksRequestThread:
    def test_immediate_exit_child_with_large_payload_does_not_raise(self) -> None:
        config = UserConfig(commands={"quick": ["true"]})
        large_payload = "x" * (200 * 1024)

        result = run_named_command("quick", config, large_payload, timeout=1.0)

        assert result is True

    def test_child_never_reading_stdin_returns_without_blocking(self) -> None:
        config = UserConfig(commands={"sleeper": ["sleep", "5"]})
        large_payload = "x" * (200 * 1024)

        started = time.monotonic()
        result = run_named_command("sleeper", config, large_payload, timeout=0.2)
        elapsed = time.monotonic() - started

        assert result is True
        assert elapsed < 0.5


class TestRunStripsProviderKeyEnv:
    def test_child_env_excludes_provider_key_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECRET_API_KEY", "sekrit")
        config = UserConfig(
            commands={"notify": ["/bin/echo", "hi"]},
            providers={"anthropic": ProviderConfig(key_env="SECRET_API_KEY")},
        )
        fake_process = MagicMock()
        fake_process.stdin = MagicMock()

        with patch("subprocess.Popen", return_value=fake_process) as popen:
            run_named_command("notify", config, "{}", timeout=1.0)

        _, kwargs = popen.call_args
        child_env = kwargs["env"]
        assert "SECRET_API_KEY" not in child_env
        assert child_env.get("PATH") == os.environ.get("PATH")

    def test_child_env_excludes_credential_lookalike_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sekrit")
        monkeypatch.setenv("FOO_API_KEY", "sekrit")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sekrit")
        config = UserConfig(commands={"notify": ["/bin/echo", "hi"]})
        fake_process = MagicMock()
        fake_process.stdin = MagicMock()

        with patch("subprocess.Popen", return_value=fake_process) as popen:
            run_named_command("notify", config, "{}", timeout=1.0)

        _, kwargs = popen.call_args
        child_env = kwargs["env"]
        leaked = {"ANTHROPIC_AUTH_TOKEN", "FOO_API_KEY", "AWS_SECRET_ACCESS_KEY"} & set(
            child_env
        )
        assert leaked == set()
        assert child_env.get("PATH") == os.environ.get("PATH")
