"""Tests for the DecideRule | RewriteRule discriminated union (AC-1, AC-2, AC-12 support)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vaudeville.rules import DecideRule, RewriteRule, parse_rule


class TestUnionDiscrimination:
    def test_decide_valid(self) -> None:
        rule = parse_rule(
            {
                "type": "decide",
                "name": "git-gate",
                "event": "Stop",
                "prompt": "classify this",
                "outcomes": ["violation", "clean"],
                "on": {"violation": "block", "clean": "allow"},
            }
        )
        assert isinstance(rule, DecideRule)
        assert rule.name == "git-gate"
        assert rule.on["violation"].action == "block"

    def test_rewrite_valid(self) -> None:
        rule = parse_rule(
            {
                "type": "rewrite",
                "name": "trim-secrets",
                "event": "PreToolUse",
                "matcher": "Write",
                "prompt": "strip secrets",
                "target": ["tool_input.content"],
            }
        )
        assert isinstance(rule, RewriteRule)
        assert rule.target == ["tool_input.content"]

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "classify",
                    "name": "old",
                    "event": "Stop",
                    "prompt": "p",
                }
            )

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "decide",
                    "name": "x",
                    "event": "Stop",
                    "prompt": "p",
                    "outcomes": ["a"],
                    "threshold": 0.5,
                }
            )


class TestTypesafeReasonValidator:
    def test_typesafe_free_text_reason_rejected_with_rule_name(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_rule(
                {
                    "type": "decide",
                    "name": "jev-judge",
                    "event": "Stop",
                    "model": "typesafe:jev-1.13",
                    "prompt": "p",
                    "outcomes": ["violation", "clean"],
                    "reason": "text",
                }
            )
        assert "jev-judge" in str(exc_info.value)

    def test_typesafe_with_reasons_buckets_is_fine(self) -> None:
        rule = parse_rule(
            {
                "type": "decide",
                "name": "jev-judge",
                "event": "Stop",
                "model": "typesafe:jev-1.13",
                "prompt": "p",
                "outcomes": ["violation", "clean"],
                "reasons": {"asks-permission": "Asks permission instead of acting"},
            }
        )
        assert isinstance(rule, DecideRule)
        assert rule.reasons == {"asks-permission": "Asks permission instead of acting"}

    def test_non_typesafe_model_may_declare_free_text_reason(self) -> None:
        rule = parse_rule(
            {
                "type": "decide",
                "name": "gpt-judge",
                "event": "Stop",
                "model": "openai:gpt-5",
                "prompt": "p",
                "outcomes": ["violation", "clean"],
                "reason": "text",
            }
        )
        assert isinstance(rule, DecideRule)
        assert rule.reason == "text"


class TestRewriteBashTargetRejected:
    def test_bash_target_rejected_with_bash_matcher(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-bash",
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "prompt": "p",
                    "target": ["tool_input.command"],
                }
            )

    def test_bash_target_rejected_with_bare_command_path_no_matcher(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-bare",
                    "event": "PreToolUse",
                    "prompt": "p",
                    "target": ["tool_input.command"],
                }
            )

    def test_bash_command_subpath_target_rejected_at_load(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-subpath",
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "prompt": "p",
                    "target": ["tool_input.command.x"],
                }
            )

    def test_non_command_target_with_bash_matcher_is_fine(self) -> None:
        rule = parse_rule(
            {
                "type": "rewrite",
                "name": "rewrite-desc",
                "event": "PreToolUse",
                "matcher": "Bash",
                "prompt": "p",
                "target": ["tool_input.description"],
            }
        )
        assert isinstance(rule, RewriteRule)

    def test_non_command_target_without_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-noprefix",
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "prompt": "p",
                    "target": ["command"],
                }
            )


class TestInvalidTierRejected:
    def test_decide_rule_invalid_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "decide",
                    "name": "x",
                    "event": "Stop",
                    "prompt": "p",
                    "outcomes": ["a"],
                    "tier": "critical",
                }
            )

    def test_rewrite_rule_invalid_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "x",
                    "event": "PreToolUse",
                    "prompt": "p",
                    "target": ["tool_input.description"],
                    "tier": "critical",
                }
            )


class TestUnknownActionRejected:
    def test_unknown_action_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "decide",
                    "name": "x",
                    "event": "Stop",
                    "prompt": "p",
                    "outcomes": ["a"],
                    "on": {"a": "explode"},
                }
            )


class TestActionShorthand:
    def test_string_shorthand_expands_to_parameterless_action(self) -> None:
        rule = parse_rule(
            {
                "type": "decide",
                "name": "x",
                "event": "Stop",
                "prompt": "p",
                "outcomes": ["a", "b"],
                "on": {"a": "warn", "b": {"action": "rewrite", "rule": "fix-it"}},
            }
        )
        assert isinstance(rule, DecideRule)
        assert rule.on["a"].action == "warn"
        assert rule.on["a"].rule is None
        assert rule.on["b"].action == "rewrite"
        assert rule.on["b"].rule == "fix-it"
