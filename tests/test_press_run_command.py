"""Adversarial attack on `run` command argv handling (AC-11, AC-12).

No argv from a rule may ever reach `subprocess.Popen`; only a named
lookup into the user's `commands:` config may supply argv, and only
that argv may start a process.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from vaudeville.server.effects.run_command import run_named_command
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.user_config import UserConfig, load_user_config

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import make_request as _request
from _hook_helpers import write_rule as _write_rule


class _PopenRecorder:
    """Stands in for `subprocess.Popen`; never starts a real process."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, argv: Any, **kwargs: Any) -> "_FakeProcess":
        self.calls.append(argv)
        return _FakeProcess()


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()

    def wait(self, timeout: float | None = None) -> None:
        del timeout

    def kill(self) -> None:
        pass


class _FakeStdin:
    def write(self, data: bytes) -> None:
        del data

    def close(self) -> None:
        pass


class TestRuleLevelArgvRejectedAtLoad:
    """A rule may never carry argv/command/commands at the rule level (AC-12)."""

    @pytest.mark.parametrize("forbidden_key", ["argv", "command", "commands"])
    def test_rule_level_forbidden_key_never_starts_a_process(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        forbidden_key: str,
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        forbidden_value = "['rm', '-rf', '/']" if forbidden_key != "commands" else "{}"
        _write_rule(
            tmp_path,
            "hostile",
            f"""
type: decide
name: hostile
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {{action: run, command: notify}}
tier: block
{forbidden_key}: {forbidden_value}
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        # The malformed rule fails to load and is skipped; the pipeline
        # still fails open, and no process is ever started.
        assert result["exit_code"] == 0
        assert recorder.calls == []


class TestNestedActionArgvRejectedAtLoad:
    """`argv` nested inside an `on:` action object must not load or run (AC-12)."""

    def test_run_action_with_nested_argv_field_never_starts_a_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        _write_rule(
            tmp_path,
            "hostile-nested",
            """
type: decide
name: hostile-nested
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: run, command: notify, argv: ["rm", "-rf", "/"]}
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        # `Action` forbids extra fields, so the rule fails to parse and is
        # skipped by the loader; the pipeline still allows, no process runs.
        assert result["exit_code"] == 0
        assert recorder.calls == []


class TestNamedCommandArgvIsUserConfigOnly:
    """A `run: {command: name}` action's argv comes only from `config.commands`."""

    def test_run_action_starts_process_with_exact_configured_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        configured_argv = ["/usr/local/bin/notify", "--flag", "value"]
        config = UserConfig(commands={"notify": configured_argv})

        ok = run_named_command("notify", config, "{}", timeout=1.0)

        assert ok is True
        assert len(recorder.calls) == 1
        assert recorder.calls[0] == configured_argv
        assert (
            recorder.calls[0] is configured_argv or recorder.calls[0] == configured_argv
        )

    def test_run_action_with_unknown_command_name_never_starts_a_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        config = UserConfig(commands={"notify": ["/usr/local/bin/notify"]})

        ok = run_named_command("does-not-exist", config, "{}", timeout=1.0)

        assert ok is False
        assert recorder.calls == []


class TestUserConfigArgvAsStringNotList:
    """A user config where a command's argv is a string, not a list (AC-11/12)."""

    def test_string_argv_in_config_file_fails_closed_without_starting_a_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        config_path = tmp_path / "config"
        config_path.write_text(
            yaml.safe_dump({"commands": {"notify": "/usr/local/bin/notify"}})
        )

        with pytest.raises(Exception):
            load_user_config(config_path)

        # Whether load_user_config raises or the pipeline's outer handler
        # catches it, no process may ever be started from a malformed
        # (non-list) command entry.
        assert recorder.calls == []

    def test_string_argv_config_reached_through_handle_hook_request_fails_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        home = tmp_path / "home-with-bad-config"
        vaudeville_dir = home / ".vaudeville"
        vaudeville_dir.mkdir(parents=True)
        (vaudeville_dir / "config").write_text(
            yaml.safe_dump(
                {
                    "default_model": "fake:model",
                    "providers": {"fake": {"key_env": "FAKE_KEY"}},
                    "commands": {"notify": "/usr/local/bin/notify"},
                }
            )
        )
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("FAKE_KEY", "x")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "no-plugin-root"))
        _write_rule(
            tmp_path,
            "warn-run",
            """
type: decide
name: warn-run
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: run, command: notify}
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        # config=None forces handle_hook_request to call load_user_config()
        # itself, inside its fail-open try/except.
        result = handle_hook_request(_request(tmp_path), config=None, decide_fn=fn)

        assert result["exit_code"] == 0
        assert recorder.calls == []


class TestConfigFileUnreadable:
    """An unreadable or garbage config file must fail open, never start a process."""

    def test_garbage_yaml_config_file_fails_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        config_path = tmp_path / "config"
        config_path.write_text(": : : not yaml : : [[[")

        with pytest.raises(Exception):
            load_user_config(config_path)
        assert recorder.calls == []

    def test_permission_denied_config_file_fails_open_through_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        home = tmp_path / "home-with-locked-config"
        vaudeville_dir = home / ".vaudeville"
        vaudeville_dir.mkdir(parents=True)
        config_path = vaudeville_dir / "config"
        config_path.write_text("default_model: fake:model\n")
        os.chmod(config_path, 0o000)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "no-plugin-root"))
        _write_rule(
            tmp_path,
            "warn-run",
            """
type: decide
name: warn-run
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: run, command: notify}
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        try:
            result = handle_hook_request(_request(tmp_path), config=None, decide_fn=fn)
        finally:
            os.chmod(config_path, 0o644)

        assert result["exit_code"] == 0
        assert recorder.calls == []

    def test_directory_in_place_of_config_file_yields_empty_config(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "config"
        config_path.mkdir()

        config = load_user_config(config_path)

        assert config == UserConfig()
