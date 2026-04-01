"""Tests for hooks/runner.py — hook execution pipeline."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(PROJECT_ROOT, "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import runner  # noqa: E402


class TestFindProjectRoot:
    def test_returns_path_in_git_repo(self) -> None:
        result = runner._find_project_root()
        assert result is not None
        assert os.path.isdir(result)

    def test_returns_none_on_oserror(self) -> None:
        with patch("runner.subprocess.run", side_effect=OSError):
            assert runner._find_project_root() is None

    def test_returns_none_on_timeout(self) -> None:
        with patch(
            "runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            assert runner._find_project_root() is None

    def test_returns_none_on_nonzero_exit(self) -> None:
        with patch("runner.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            assert runner._find_project_root() is None


class TestLoadRule:
    def test_loads_existing_rule(self) -> None:
        rule = runner.load_rule("violation-detector")
        assert rule is not None
        assert "name" in rule

    def test_returns_none_for_missing_rule(self, capsys) -> None:
        rule = runner.load_rule("nonexistent-rule-xyz")
        assert rule is None
        assert "nonexistent-rule-xyz" in capsys.readouterr().err


class TestExtractField:
    def test_simple_key(self) -> None:
        assert runner.extract_field({"body": "hello"}, "body") == "hello"

    def test_dotted_path(self) -> None:
        data = {"tool_input": {"body": "text"}}
        assert runner.extract_field(data, "tool_input.body") == "text"

    def test_missing_key_returns_empty(self) -> None:
        assert runner.extract_field({}, "missing.key") == ""

    def test_non_dict_intermediate_returns_empty(self) -> None:
        assert runner.extract_field({"a": "not-a-dict"}, "a.b") == ""

    def test_none_value_returns_empty(self) -> None:
        assert runner.extract_field({"key": None}, "key") == ""

    def test_integer_value_converted_to_str(self) -> None:
        assert runner.extract_field({"num": 42}, "num") == "42"


class TestExtractText:
    def test_field_context_dict(self) -> None:
        rule: dict = {"context": [{"field": "body"}]}
        assert runner.extract_text({"body": "some text"}, rule) == "some text"

    def test_string_context(self) -> None:
        rule: dict = {"context": ["body"]}
        assert runner.extract_text({"body": "some text"}, rule) == "some text"

    def test_empty_context_returns_empty(self) -> None:
        rule: dict = {"context": []}
        assert runner.extract_text({}, rule) == ""

    def test_no_context_key_returns_empty(self) -> None:
        assert runner.extract_text({}, {}) == ""

    def test_falls_through_to_second_entry(self) -> None:
        rule: dict = {"context": [{"field": "missing"}, {"field": "body"}]}
        assert runner.extract_text({"body": "found"}, rule) == "found"

    def test_first_non_empty_wins(self) -> None:
        rule: dict = {"context": [{"field": "first"}, {"field": "second"}]}
        data = {"first": "wins", "second": "loses"}
        assert runner.extract_text(data, rule) == "wins"


class TestVerdictToHookResponse:
    def _rule(self, action: str = "block") -> dict:
        return {"name": "test-rule", "message": "Caught: {reason}", "action": action}

    def test_block_returns_decision_block(self) -> None:
        resp = runner.verdict_to_hook_response(self._rule("block"), "hedging", "block")
        assert resp["decision"] == "block"
        assert "Caught: hedging" in resp["systemMessage"]

    def test_warn_returns_warning_system_message(self) -> None:
        resp = runner.verdict_to_hook_response(self._rule("warn"), "mild issue", "warn")
        assert resp["decision"] == "block"
        assert "Warning" in resp["systemMessage"]
        assert "mild issue" in resp["systemMessage"]

    def test_log_action_returns_empty_dict(self, capsys) -> None:
        resp = runner.verdict_to_hook_response(self._rule("log"), "logged", "log")
        assert resp == {}
        assert "logged" in capsys.readouterr().err

    def test_default_message_uses_reason(self) -> None:
        rule = {"name": "r", "action": "block"}  # no message key
        resp = runner.verdict_to_hook_response(rule, "my reason", "block")
        assert "my reason" in resp["systemMessage"]

    def test_reason_interpolated_in_message(self) -> None:
        rule = {"name": "r", "message": "Issue: {reason}", "action": "block"}
        resp = runner.verdict_to_hook_response(rule, "hedging detected", "block")
        assert resp["systemMessage"] == "Issue: hedging detected"


class TestRunPipeline:
    """Tests for _run() — the main execution loop."""

    def _run_with_stdin(self, argv: list[str], stdin_data: dict | None = None) -> int:
        """Helper: set argv, mock stdin, call _run(), catch SystemExit."""
        stdin_str = json.dumps(stdin_data) if stdin_data is not None else ""
        with (
            patch.object(sys, "argv", argv),
            patch("sys.stdin", io.StringIO(stdin_str)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        return exc_info.value.code  # type: ignore[return-value]

    def test_no_rules_exits_0(self) -> None:
        code = self._run_with_stdin(["runner.py"])
        assert code == 0

    def test_bad_json_stdin_exits_0(self) -> None:
        with (
            patch.object(sys, "argv", ["runner.py", "violation-detector"]),
            patch("sys.stdin", io.StringIO("not json {")),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0

    def test_no_socket_exits_0(self) -> None:
        data = {"session_id": "no-socket-test-xyz", "last_assistant_message": "hi"}
        code = self._run_with_stdin(["runner.py", "violation-detector"], data)
        assert code == 0

    def test_clean_verdict_exits_0(self, capsys) -> None:
        from vaudeville.core.protocol import ClassifyResponse

        hook_input = {
            "session_id": "clean-session",
            "last_assistant_message": "A" * 200,
        }
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="clean", reason="ok", action="block"
        )
        with (
            patch.object(sys, "argv", ["runner.py", "violation-detector"]),
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("runner.os.path.exists", return_value=True),
            patch("runner.VaudevilleClient", return_value=mock_client),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert json.loads(out.strip()) == {}

    def test_violation_verdict_outputs_block_response(self, capsys) -> None:
        from vaudeville.core.protocol import ClassifyResponse

        hook_input = {
            "session_id": "violation-session",
            "last_assistant_message": "A" * 200,
        }
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="violation", reason="hedging detected", action="block"
        )
        with (
            patch.object(sys, "argv", ["runner.py", "violation-detector"]),
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("runner.os.path.exists", return_value=True),
            patch("runner.VaudevilleClient", return_value=mock_client),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        resp = json.loads(out.strip())
        assert resp["decision"] == "block"

    def test_null_classify_result_continues_to_next_rule(self, capsys) -> None:
        hook_input = {
            "session_id": "null-result-session",
            "last_assistant_message": "A" * 200,
        }
        mock_client = MagicMock()
        mock_client.classify.return_value = None
        with (
            patch.object(sys, "argv", ["runner.py", "violation-detector"]),
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("runner.os.path.exists", return_value=True),
            patch("runner.VaudevilleClient", return_value=mock_client),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0

    def test_text_below_min_length_skips_rule(self, capsys) -> None:
        hook_input = {
            "session_id": "short-text-session",
            "last_assistant_message": "short",
        }
        mock_client = MagicMock()
        with (
            patch.object(sys, "argv", ["runner.py", "violation-detector"]),
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("runner.os.path.exists", return_value=True),
            patch("runner.VaudevilleClient", return_value=mock_client),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0
        mock_client.classify.assert_not_called()

    def test_missing_rule_skips_silently(self, capsys) -> None:
        hook_input = {
            "session_id": "missing-rule-session",
            "last_assistant_message": "A" * 200,
        }
        with (
            patch.object(sys, "argv", ["runner.py", "nonexistent-rule-xyz"]),
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("runner.os.path.exists", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0

    def test_main_catches_unexpected_exception(self, capsys) -> None:
        with patch("runner._run", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                runner.main()
        assert exc_info.value.code == 0
        assert "fail open" in capsys.readouterr().err
