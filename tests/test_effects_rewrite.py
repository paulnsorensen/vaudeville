"""Tests for the rewrite applier and the rewrite-to-feedback downgrade (AC-8, AC-9)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vaudeville.rules import parse_rule
from vaudeville.server.effects import apply_rewrite, rewrite_or_feedback
from vaudeville.server.harness import HookEvent

REWRITE_RULE = {
    "type": "rewrite",
    "name": "trim-secrets",
    "event": "PreToolUse",
    "matcher": "Write",
    "prompt": "Strip any secrets from the content.",
    "target": ["tool_input.content"],
}


class TestApplyRewriteTargetOnly:
    def test_target_only(self) -> None:
        records: list[dict[str, object]] = []
        tool_input = {"content": "secret stuff", "file_path": "/tmp/x"}

        updated = apply_rewrite(
            tool_input,
            ["tool_input.content"],
            {"tool_input.content": "clean stuff", "tool_input.file_path": "/tmp/evil"},
            rule_name="trim-secrets",
            log=records.append,
        )

        assert updated["content"] == "clean stuff"
        assert updated["file_path"] == "/tmp/x"
        assert len(records) == 1
        assert records[0]["path"] == "tool_input.content"

    def test_creates_missing_nested_path_and_reports_no_before_value(self) -> None:
        records: list[dict[str, object]] = []
        tool_input: dict[str, object] = {}

        updated = apply_rewrite(
            tool_input,
            ["tool_input.nested.field"],
            {"tool_input.nested.field": "new value"},
            rule_name="trim-secrets",
            log=records.append,
        )

        assert updated["nested"] == {"field": "new value"}
        assert records[0]["before"] is None

    def test_caller_nested_mapping_not_mutated(self) -> None:
        records: list[dict[str, object]] = []
        tool_input = {"a": {"b": "old"}}

        updated = apply_rewrite(
            tool_input,
            ["tool_input.a.b"],
            {"tool_input.a.b": "new"},
            rule_name="trim-secrets",
            log=records.append,
        )

        assert updated["a"]["b"] == "new"
        assert tool_input["a"]["b"] == "old"


class TestBashTargetRejectedAtLoad:
    def test_bash_target_rejected(self) -> None:
        bash_rule = {
            "type": "rewrite",
            "name": "rewrite-bash",
            "event": "PreToolUse",
            "matcher": "Bash",
            "prompt": "Rewrite the command.",
            "target": ["tool_input.command"],
        }

        with pytest.raises(ValidationError, match="cannot be rewritten"):
            parse_rule(bash_rule)


class TestBeforeAfterLogged:
    def test_before_after_logged(self) -> None:
        records: list[dict[str, object]] = []
        tool_input = {"content": "before value"}

        apply_rewrite(
            tool_input,
            ["tool_input.content"],
            {"tool_input.content": "after value"},
            rule_name="trim-secrets",
            log=records.append,
        )

        assert records == [
            {
                "rule": "trim-secrets",
                "path": "tool_input.content",
                "before": "before value",
                "after": "after value",
            }
        ]


class TestNoToolInputFeedback:
    def test_no_tool_input_feedback(self) -> None:
        records: list[dict[str, object]] = []
        event = HookEvent(
            harness="claude-code",
            event="Stop",
            tool_name=None,
            tool_input=None,
            cwd="/tmp",
            raw={},
            text="the model's rewritten text",
        )

        outcome = rewrite_or_feedback(
            event,
            "the model's rewritten text",
            rule_name="trim-secrets",
            log=records.append,
        )

        assert outcome is not None
        assert outcome.action.action == "feedback"
        assert outcome.updated_input is None
        assert outcome.message == "the model's rewritten text"
        assert records == [
            {
                "from": "rewrite",
                "to": "feedback",
                "event": "Stop",
                "rule": "trim-secrets",
            }
        ]

    def test_with_tool_input_returns_none(self) -> None:
        records: list[dict[str, object]] = []
        event = HookEvent(
            harness="claude-code",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"content": "x"},
            cwd="/tmp",
            raw={},
        )

        outcome = rewrite_or_feedback(
            event,
            "text",
            rule_name="trim-secrets",
            log=records.append,
        )

        assert outcome is None
        assert records == []
