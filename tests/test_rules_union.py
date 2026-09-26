"""Tests for the DecideRule | RewriteRule discriminated union (AC-1, AC-2, AC-12 support)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vaudeville.rules import DecideRule, RewriteRule, UnsureGate, parse_rule


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


class TestRewriteTargetGuard:
    @pytest.mark.parametrize(
        "target",
        [
            "tool_input.file_path",
            "tool_input.url",
            "tool_input.path",
            "tool_input.code",
            "tool_input.script",
            "tool_input.query",
            "tool_input.args.url",
            "tool_input.code.body",
        ],
    )
    def test_io_or_exec_target_rejected(self, target: str) -> None:
        """F22: a rewrite must not redirect where a tool reads, writes, or runs."""
        with pytest.raises(ValidationError, match="routes I/O or executes"):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "redirect",
                    "event": "PreToolUse",
                    "matcher": "Write",
                    "prompt": "p",
                    "target": ["tool_input.content", target],
                }
            )


class TestActionParameters:
    @pytest.mark.parametrize(
        "action",
        [
            "rewrite",
            "escalate",
            "run",
            {"action": "rewrite"},
            {"action": "escalate", "command": "x"},
            {"action": "run", "rule": "x"},
        ],
    )
    def test_action_without_its_parameter_rejected(self, action: object) -> None:
        """F10: a typo in `rule:`/`command:` must fail loud, not disable a block."""
        with pytest.raises(ValidationError, match="requires a"):
            parse_rule(
                {
                    "type": "decide",
                    "name": "x",
                    "event": "Stop",
                    "prompt": "p",
                    "outcomes": ["violation"],
                    "on": {"violation": action},
                }
            )

    @pytest.mark.parametrize(
        "action",
        [
            {"action": "rewrite", "rule": "fix"},
            {"action": "escalate", "rule": "judge"},
            {"action": "run", "command": "notify"},
        ],
    )
    def test_action_with_its_parameter_accepted(self, action: object) -> None:
        rule = parse_rule(
            {
                "type": "decide",
                "name": "x",
                "event": "Stop",
                "prompt": "p",
                "outcomes": ["violation"],
                "on": {"violation": action},
            }
        )
        assert isinstance(rule, DecideRule)


class TestTypesafeReasonValidator:
    def test_typesafe_free_text_reason_rejected_with_rule_name(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_rule(
                {
                    "type": "decide",
                    "name": "jev-judge",
                    "event": "Stop",
                    "model": "typesafe:jev-1.13.0",
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
                "model": "typesafe:jev-1.13.0",
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

    def test_argv_leaf_target_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-argv",
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "prompt": "p",
                    "target": ["tool_input.argv"],
                }
            )

    def test_commands_leaf_target_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-commands",
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "prompt": "p",
                    "target": ["tool_input.commands"],
                }
            )

    def test_content_leaf_target_accepted(self) -> None:
        rule = parse_rule(
            {
                "type": "rewrite",
                "name": "rewrite-content",
                "event": "PreToolUse",
                "prompt": "p",
                "target": ["tool_input.content"],
            }
        )
        assert isinstance(rule, RewriteRule)

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


class TestOutcomesCoverage:
    """an `on` key or test-case outcome outside `outcomes` fails loud."""

    def test_bad_on_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="git-gate") as exc_info:
            parse_rule(
                {
                    "type": "decide",
                    "name": "git-gate",
                    "event": "Stop",
                    "prompt": "p",
                    "outcomes": ["violation", "clean"],
                    "on": {"violaiton": "block"},
                }
            )
        assert "violaiton" in str(exc_info.value)

    def test_bad_test_case_outcome_rejected(self) -> None:
        with pytest.raises(ValidationError, match="git-gate") as exc_info:
            parse_rule(
                {
                    "type": "decide",
                    "name": "git-gate",
                    "event": "Stop",
                    "prompt": "p",
                    "outcomes": ["violation", "clean"],
                    "on": {"violation": "block"},
                    "test_cases": [{"text": "t", "outcome": "violaiton"}],
                }
            )
        assert "violaiton" in str(exc_info.value)


class TestTargetHasLeafAllSegments:
    def test_leaf_buried_mid_path_rejected(self) -> None:
        """every path segment is checked, not just the first and last."""
        with pytest.raises(ValidationError, match="resolves to a Bash command"):
            parse_rule(
                {
                    "type": "rewrite",
                    "name": "rewrite-buried",
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "prompt": "p",
                    "target": ["tool_input.options.command.0"],
                }
            )


class TestUnsureGateLoadValidation:
    """AC-6: unsure: is rejected at load unless the rule declares an
    explicit typesafe: model, below is in (0, 1], and any outcomes filter
    entry is itself one of the rule's outcomes."""

    def _rule(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "type": "decide",
            "name": "jev-judge",
            "event": "Stop",
            "model": "typesafe:jev-1.13",
            "prompt": "p",
            "outcomes": ["violation", "clean"],
            "unsure": {
                "below": 0.5,
                "action": {"action": "escalate", "rule": "human-review"},
            },
        }
        base.update(overrides)
        return base

    def test_unsure_requires_typesafe_model_rejected(self) -> None:
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(self._rule(model="openai:gpt-5"))
        assert "typesafe" in str(exc_info.value)

    def test_unsure_requires_typesafe_model_rejected_when_inherited_default(self) -> None:
        """An absent `model:` (inherited config default) is rejected the
        same as an explicit non-typesafe model: `unsure:` requires the
        rule's own model to be explicit typesafe."""
        rule = self._rule()
        del rule["model"]
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(rule)
        assert "typesafe" in str(exc_info.value)

    def test_unsure_below_out_of_range_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(
                self._rule(
                    unsure={
                        "below": 0,
                        "action": {"action": "escalate", "rule": "human-review"},
                    }
                )
            )
        assert "below" in str(exc_info.value)

    def test_unsure_below_out_of_range_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(
                self._rule(
                    unsure={
                        "below": 1.5,
                        "action": {"action": "escalate", "rule": "human-review"},
                    }
                )
            )
        assert "below" in str(exc_info.value)

    def test_unsure_unknown_outcome_filter_rejected(self) -> None:
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(
                self._rule(
                    unsure={
                        "below": 0.5,
                        "action": {"action": "escalate", "rule": "human-review"},
                        "outcomes": ["unknown-outcome"],
                    }
                )
            )
        assert "unknown-outcome" in str(exc_info.value)

    def test_unsure_empty_outcome_filter_rejected(self) -> None:
        """An empty filter would gate no outcome, so the gate is inert."""
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(
                self._rule(
                    unsure={
                        "below": 0.5,
                        "action": {"action": "escalate", "rule": "human-review"},
                        "outcomes": [],
                    }
                )
            )
        assert "unsure.outcomes must be non-empty" in str(exc_info.value)

    def test_unsure_valid_outcome_filter_loads(self) -> None:
        rule = parse_rule(
            self._rule(
                unsure={
                    "below": 0.5,
                    "action": {"action": "escalate", "rule": "human-review"},
                    "outcomes": ["violation"],
                }
            )
        )
        assert isinstance(rule, DecideRule)
        assert rule.unsure is not None
        assert rule.unsure.outcomes == ["violation"]

    def test_unsure_self_escalate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="jev-judge") as exc_info:
            parse_rule(
                self._rule(
                    unsure={
                        "below": 0.5,
                        "action": {"action": "escalate", "rule": "jev-judge"},
                    }
                )
            )
        assert "escalate to itself" in str(exc_info.value)

    def test_unsure_valid_typesafe_rule_loads(self) -> None:
        rule = parse_rule(self._rule())
        assert isinstance(rule, DecideRule)
        assert isinstance(rule.unsure, UnsureGate)
        assert rule.unsure.below == 0.5
        assert rule.unsure.action.action == "escalate"
