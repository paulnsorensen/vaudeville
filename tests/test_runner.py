"""Tests for hooks/runner.py — hook execution pipeline."""

from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(PROJECT_ROOT, "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import runner  # noqa: E402


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
        assert "decision" not in resp
        assert "vaudeville hook [test-rule] warned about:" in resp["systemMessage"]
        assert "mild issue" in resp["systemMessage"]

    def test_default_message_uses_reason(self) -> None:
        resp = runner.verdict_to_hook_response("r", "{reason}", "my reason", "block")
        assert "my reason" in resp["systemMessage"]

    def test_reason_interpolated_in_message(self) -> None:
        resp = runner.verdict_to_hook_response(
            "r", "Issue: {reason}", "hedging detected", "block"
        )
        assert "Issue: hedging detected" in resp["systemMessage"]
        assert "vaudeville hook [r] prevented response:" in resp["systemMessage"]


class TestSkipEnvVar:
    """Tests for VAUDEVILLE_SKIP bypass."""

    def test_skip_exits_immediately(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.dict(os.environ, {"VAUDEVILLE_SKIP": "1"}),
            patch.object(sys, "argv", ["runner.py", "--event", "Stop"]),
            patch("sys.stdin", io.StringIO('{"body": "text"}')),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run()
        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_skip_not_set_proceeds(self) -> None:
        """Without VAUDEVILLE_SKIP, runner proceeds normally."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(sys, "argv", ["runner.py", "--event", "Stop"]),
            patch("sys.stdin", io.StringIO('{"body": "text"}')),
            patch("runner._load_rules_for_event", return_value=[]),
            pytest.raises(SystemExit) as exc_info,
        ):
            env = os.environ.copy()
            env.pop("VAUDEVILLE_SKIP", None)
            with patch.dict(os.environ, env, clear=True):
                runner._run()
        assert exc_info.value.code == 0

    def test_skip_value_must_be_1(self) -> None:
        """VAUDEVILLE_SKIP=true or other values don't trigger skip."""
        with (
            patch.dict(os.environ, {"VAUDEVILLE_SKIP": "true"}),
            patch.object(sys, "argv", ["runner.py", "--event", "Stop"]),
            patch("sys.stdin", io.StringIO('{"body": "text"}')),
            patch("runner._load_rules_for_event", return_value=[]),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run()
        assert exc_info.value.code == 0


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

        from unittest.mock import ANY

        mock_client.classify.assert_called_once_with(
            ANY, rule="test-hedging", prefix_len=ANY, tier="block", input_text=ANY
        )

    def test_shadow_tier_logs_but_passes(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Shadow tier skips violation and returns {} (pass)."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        mock_rule = Rule(
            name="test-shadow",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
            tier="shadow",
        )
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="violation", reason="hedging detected", confidence=0.9
        )
        mock_client.condense.side_effect = lambda text: text
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[mock_rule]),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "{}"

    def test_shadow_tier_debug_logs_when_enabled(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Shadow tier emits debug log when VAUDEVILLE_DEBUG=1."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        mock_rule = Rule(
            name="test-shadow",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
            tier="shadow",
        )
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="violation", reason="hedging detected", confidence=0.9
        )
        mock_client.condense.side_effect = lambda text: text
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[mock_rule]),
            patch.dict(os.environ, {"VAUDEVILLE_DEBUG": "1"}),
            patch.object(runner, "_DEBUG", True),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "shadow test-shadow" in captured.err

    def test_log_tier_logs_to_stderr_and_continues(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Log tier emits to stderr and continues the rule loop (does not exit)."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        log_rule = Rule(
            name="test-log",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
            tier="log",
        )
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="violation", reason="log fired", confidence=0.9
        )
        mock_client.condense.side_effect = lambda text: text
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[log_rule]),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "{}"
        assert "[vaudeville] test-log: log fired" in captured.err

    def test_warn_tier_omits_decision_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Warn tier emits systemMessage-only response (no decision field).

        Claude Code's Stop hook schema only accepts decision="approve"|"block";
        a "warn" value would fail JSON validation. Warnings must ride on
        systemMessage with no decision field.
        """
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        mock_rule = Rule(
            name="test-warn",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
            tier="warn",
        )
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="violation", reason="hedging detected", confidence=0.9
        )
        mock_client.condense.side_effect = lambda text: text
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[mock_rule]),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        response = json.loads(captured.out.strip())
        assert "decision" not in response
        assert "warned about" in response["systemMessage"]

    def test_block_tier_emits_block_decision(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Block tier (default) emits a Claude Code block decision."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        mock_rule = Rule(
            name="test-block",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
            tier="block",
        )
        mock_client = MagicMock()
        mock_client.classify.return_value = ClassifyResponse(
            verdict="violation", reason="hedging detected", confidence=0.9
        )
        mock_client.condense.side_effect = lambda text: text
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[mock_rule]),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        response = json.loads(captured.out.strip())
        assert response["decision"] == "block"

    def test_main_catches_unexpected_exception(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("runner._run", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                runner.main()
        assert exc_info.value.code == 0
        assert "fail open" in capsys.readouterr().err

    def test_disabled_tier_skips_inference(self) -> None:
        """Disabled rules must not invoke classify or condense — zero SLM cost."""
        from unittest.mock import MagicMock

        from vaudeville.core.rules import Rule

        mock_rule = Rule(
            name="test-disabled",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
            tier="disabled",
        )
        mock_client = MagicMock()
        hook_input = {"body": "x" * 100}

        with (
            patch("runner._load_rules_for_event", return_value=[mock_rule]),
            pytest.raises(SystemExit) as exc_info,
        ):
            runner._run_event_rules("Stop", hook_input, mock_client)

        assert exc_info.value.code == 0
        mock_client.classify.assert_not_called()
        mock_client.condense.assert_not_called()


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

        client.condense.assert_called_once_with("x" * 100)

    def test_code_blocks_stripped_before_condense(self) -> None:
        """_prepare_text runs before _maybe_condense: code blocks are gone."""
        from unittest.mock import MagicMock

        from vaudeville.core.protocol import ClassifyResponse
        from vaudeville.core.rules import Rule

        rule = Rule(
            name="test-rule",
            event="Stop",
            prompt="Check: {text}",
            context=[{"field": "body"}],
            message="{reason}",
            threshold=0.5,
        )
        client = MagicMock()
        client.condense.side_effect = lambda text: text
        client.classify.return_value = ClassifyResponse(
            verdict="clean", reason="ok", confidence=0.9
        )
        body = "prose here\n```python\ncode_block()\n```\nmore prose\n" + "x" * 100

        with (
            patch("runner._load_rules_for_event", return_value=[rule]),
            pytest.raises(SystemExit),
        ):
            runner._run_event_rules("Stop", {"body": body}, client)

        condensed_arg = client.condense.call_args[0][0]
        assert "code_block()" not in condensed_arg
        assert "prose here" in condensed_arg

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
