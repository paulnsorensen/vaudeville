"""Typed rule models: DecideRule, RewriteRule, the Rule union, and RuleSet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .actions import Action

Tier = Literal["disabled", "shadow", "log", "warn", "block"]

VALID_TIERS: tuple[Tier, ...] = get_args(Tier)


def _check_tier(value: object, rule_name: object) -> object:
    if value not in VALID_TIERS:
        raise ValueError(
            f"rule {rule_name!r}: invalid tier {value!r}, must be one of {VALID_TIERS}"
        )
    return value


# Rule-level keys that would let a rule carry command argv directly. Only a
# `run` action may reference a command, and only by name (AC-12).
_FORBIDDEN_ARGV_KEYS: tuple[str, ...] = ("argv", "command", "commands")


class DecideTestCase(BaseModel):
    """One labeled example for a decide rule."""

    model_config = ConfigDict(extra="forbid")

    text: str
    outcome: str


class DecideRule(BaseModel):
    """A rule that asks a model to pick one of `outcomes` and maps it to an action."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["decide"] = "decide"
    name: str
    event: str
    matcher: str | None = None
    model: str | None = None
    prompt: str
    outcomes: list[str] = Field(min_length=1)
    reason: Literal["text"] | None = None
    reasons: dict[str, str] | None = None
    on: dict[str, Action] = Field(default_factory=dict)
    tier: Tier = "block"
    draft: bool = False
    test_cases: list[DecideTestCase] = Field(default_factory=list)

    @field_validator("tier", mode="before")
    @classmethod
    def _validate_tier(cls, value: object, info: ValidationInfo) -> object:
        return _check_tier(value, info.data.get("name", "?"))

    @model_validator(mode="after")
    def _validate_typesafe_reason(self) -> "DecideRule":
        if self.model and self.model.startswith("typesafe:") and self.reason == "text":
            raise ValueError(
                f"rule {self.name!r}: a typesafe: model cannot declare reason: text "
                "(Jev returns typed values, not free text)"
            )
        return self

    @model_validator(mode="after")
    def _validate_outcomes_coverage(self) -> "DecideRule":
        outcomes = set(self.outcomes)
        for key in self.on:
            if key not in outcomes:
                raise ValueError(
                    f"rule {self.name!r}: on: key {key!r} is not in outcomes {self.outcomes}"
                )
        for case in self.test_cases:
            if case.outcome not in outcomes:
                raise ValueError(
                    f"rule {self.name!r}: test case outcome {case.outcome!r} is not in "
                    f"outcomes {self.outcomes}"
                )
        return self


_TOOL_INPUT_PREFIX = "tool_input."

# Leaves that route I/O or execute code; a rewrite must not redirect them.
_FORBIDDEN_IO_KEYS: tuple[str, ...] = (
    "file_path",
    "url",
    "path",
    "code",
    "script",
    "query",
)


def _target_has_leaf(target: str, leaves: tuple[str, ...]) -> bool:
    return any(segment in leaves for segment in target.split("."))


def _target_is_bash_command(target: str) -> bool:
    """Return True when any path segment of `target` is a forbidden argv
    leaf (`argv`, `command`, or `commands`). Checked unconditionally,
    regardless of matcher.
    """
    return _target_has_leaf(target, _FORBIDDEN_ARGV_KEYS)


class RewriteRule(BaseModel):
    """A rule that asks a model to rewrite one or more tool-input fields."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["rewrite"] = "rewrite"
    name: str
    event: str
    matcher: str | None = None
    model: str | None = None
    prompt: str
    target: list[str] = Field(min_length=1)
    tier: Tier = "block"
    draft: bool = False

    @field_validator("tier", mode="before")
    @classmethod
    def _validate_tier(cls, value: object, info: ValidationInfo) -> object:
        return _check_tier(value, info.data.get("name", "?"))

    @model_validator(mode="after")
    def _validate_target_no_bash(self) -> "RewriteRule":
        for target in self.target:
            if not target.startswith(_TOOL_INPUT_PREFIX):
                raise ValueError(
                    f"rule {self.name!r}: target {target!r} must start with "
                    f"{_TOOL_INPUT_PREFIX!r}"
                )
            if _target_is_bash_command(target):
                raise ValueError(
                    f"rule {self.name!r}: target {target!r} resolves to a Bash command "
                    "and cannot be rewritten"
                )
            if _target_has_leaf(target, _FORBIDDEN_IO_KEYS):
                raise ValueError(
                    f"rule {self.name!r}: target {target!r} routes I/O or executes "
                    "and cannot be rewritten"
                )
        return self


Rule = Annotated[Union[DecideRule, RewriteRule], Field(discriminator="type")]

_RULE_ADAPTER: TypeAdapter[DecideRule | RewriteRule] = TypeAdapter(Rule)


def parse_rule(data: dict[str, Any]) -> DecideRule | RewriteRule:
    """Parse and validate a raw rule mapping into a DecideRule or RewriteRule.

    Raises ValueError when a rule-level key would carry command argv
    directly (AC-12); a `run` action may only reference a named command.
    """
    carried = [key for key in _FORBIDDEN_ARGV_KEYS if key in data]
    if carried:
        name = data.get("name", "?")
        raise ValueError(
            f"rule {name!r}: rule-level key(s) {carried} would carry command argv; "
            "a `run` action may only reference a named command"
        )
    rule: DecideRule | RewriteRule = _RULE_ADAPTER.validate_python(data)
    return rule


@dataclass(frozen=True)
class RuleSet:
    """An immutable set of loaded rules."""

    rules: tuple[DecideRule | RewriteRule, ...] = ()

    def by_name(self) -> dict[str, DecideRule | RewriteRule]:
        return {rule.name: rule for rule in self.rules}

    def for_event(self, event: str) -> list[DecideRule | RewriteRule]:
        return [rule for rule in self.rules if rule.event == event]
