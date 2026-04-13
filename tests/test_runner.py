"""Tests for hooks/runner.py — hook execution pipeline."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from unittest.mock import patch

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

    def test_zero_value_preserved(self) -> None:
        assert runner.extract_field({"num": 0}, "num") == "0"

    def test_false_value_preserved(self) -> None:
        assert runner.extract_field({"flag": False}, "flag") == "False"

    def test_empty_string_preserved(self) -> None:
        assert runner.extract_field({"val": ""}, "val") == ""


class TestExtractText:
    def test_field_context_dict(self) -> None:
        context = [{"field": "body"}]
        assert (
            runner.extract_text_from_dict({"body": "some text"}, context) == "some text"
        )

    def test_string_context(self) -> None:
        context = ["body"]
        assert (
            runner.extract_text_from_dict({"body": "some text"}, context) == "some text"
        )

    def test_empty_context_returns_empty(self) -> None:
        assert runner.extract_text_from_dict({}, []) == ""

    def test_no_context_returns_empty(self) -> None:
        assert runner.extract_text_from_dict({}, []) == ""

    def test_falls_through_to_second_entry(self) -> None:
        context = [{"field": "missing"}, {"field": "body"}]
        assert runner.extract_text_from_dict({"body": "found"}, context) == "found"

    def test_first_non_empty_wins(self) -> None:
        context = [{"field": "first"}, {"field": "second"}]
        data = {"first": "wins", "second": "loses"}
        assert runner.extract_text_from_dict(data, context) == "wins"


class TestVerdictToHookResponse:
    def test_block_returns_decision_block(self) -> None:
        resp = runner.verdict_to_hook_response(
            "test-rule", "Caught: {reason}", "hedging", "block"
        )
        assert resp["decision"] == "block"
        assert "Caught: hedging" in resp["systemMessage"]

    def test_warn_returns_warning_system_message(self) -> None:
        resp = runner.verdict_to_hook_response(
            "test-rule", "Caught: {reason}", "mild issue", "warn"
        )
        assert resp["decision"] == "warn"
        assert "vaudeville hook [test-rule] warned about:" in resp["systemMessage"]
        assert "mild issue" in resp["systemMessage"]

    def test_log_action_returns_empty_dict(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resp = runner.verdict_to_hook_response(
            "test-rule", "Caught: {reason}", "logged", "log"
        )
        assert resp == {}
        assert "logged" in capsys.readouterr().err

    def test_default_message_uses_reason(self) -> None:
        resp = runner.verdict_to_hook_response("r", "{reason}", "my reason", "block")
        assert "my reason" in resp["systemMessage"]

    def test_reason_interpolated_in_message(self) -> None:
        resp = runner.verdict_to_hook_response(
            "r", "Issue: {reason}", "hedging detected", "block"
        )
        assert "Issue: hedging detected" in resp["systemMessage"]
        assert "vaudeville hook [r] prevented response:" in resp["systemMessage"]


class TestRunPipeline:
    """Tests for _run() — the main execution loop."""

    def _run_with_stdin(
        self, argv: list[str], stdin_data: dict[str, str] | None = None
    ) -> int:
        """Helper: set argv, mock stdin, call _run(), catch SystemExit."""
        stdin_str = json.dumps(stdin_data) if stdin_data is not None else ""
        with (
            patch.object(sys, "argv", argv),
            patch("sys.stdin", io.StringIO(stdin_str)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        return exc_info.value.code  # type: ignore[return-value]

    def test_no_event_exits_0(self) -> None:
        code = self._run_with_stdin(["runner.py"])
        assert code == 0

    def test_bad_json_stdin_exits_0(self) -> None:
        with (
            patch.object(sys, "argv", ["runner.py", "--event", "Stop"]),
            patch("sys.stdin", io.StringIO("not json {")),
        ):
            with pytest.raises(SystemExit) as exc_info:
                runner._run()
        assert exc_info.value.code == 0

    def test_no_matching_rules_exits_0(self) -> None:
        data = {"session_id": "test", "last_assistant_message": "hi"}
        with patch("runner._load_rules_for_event", return_value=[]):
            code = self._run_with_stdin(["runner.py", "--event", "Stop"], data)
        assert code == 0

    def test_run_event_rules_passes_rule_name_to_classify(self) -> None:
        """Verify that _run_event_rules forwards rule.name to client.classify()."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        mock_rule = Rule(
            name="test-hedging",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            action="block",
            message="{reason}",
            threshold=0.5,
        )
        mock_client = MagicMock()
        mock_client.condense.side_effect = lambda text: text
        mock_client.classify.return_value = ClassifyResponse(
            verdict="clean", reason="", confidence=0.9
        )
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[mock_rule]),
            pytest.raises(SystemExit),
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        mock_client.classify.assert_called_once()
        _, kwargs = mock_client.classify.call_args
        assert kwargs["rule"] == "test-hedging"

    def test_main_catches_unexpected_exception(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("runner._run", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                runner.main()
        assert exc_info.value.code == 0
        assert "fail open" in capsys.readouterr().err


class TestMaybeCondense:
    """Tests for _maybe_condense — SLM condensing gate."""

    def test_stop_event_calls_condense(self) -> None:
        from unittest.mock import MagicMock

        client = MagicMock()
        client.condense.return_value = "condensed"
        result = runner._maybe_condense("original text", "Stop", client)
        assert result == "condensed"
        client.condense.assert_called_once_with("original text")

    def test_non_stop_event_skips_condense(self) -> None:
        from unittest.mock import MagicMock

        client = MagicMock()
        result = runner._maybe_condense("original text", "PreToolUse", client)
        assert result == "original text"
        client.condense.assert_not_called()

    def test_unknown_event_skips_condense(self) -> None:
        from unittest.mock import MagicMock

        client = MagicMock()
        result = runner._maybe_condense("text", "PostToolUse", client)
        assert result == "text"
        client.condense.assert_not_called()

    def test_run_event_rules_condenses_for_stop(self) -> None:
        """End-to-end: _run_event_rules calls condense for Stop events."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        rule = Rule(
            name="test-rule",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            action="block",
            message="{reason}",
            threshold=0.5,
        )
        client = MagicMock()
        client.condense.return_value = "condensed body"
        client.classify.return_value = ClassifyResponse(
            verdict="clean", reason="ok", confidence=0.9
        )
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[rule]),
            pytest.raises(SystemExit),
        ):
            runner._run_event_rules("Stop", hook_input, client)

        client.condense.assert_called_once()

    def test_run_event_rules_skips_condense_for_pretooluse(self) -> None:
        """_run_event_rules does NOT condense for PreToolUse events."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        rule = Rule(
            name="test-rule",
            event="PreToolUse",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            action="block",
            message="{reason}",
            threshold=0.5,
        )
        client = MagicMock()
        client.classify.return_value = ClassifyResponse(
            verdict="clean", reason="ok", confidence=0.9
        )
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[rule]),
            pytest.raises(SystemExit),
        ):
            runner._run_event_rules("PreToolUse", hook_input, client)

        client.condense.assert_not_called()
