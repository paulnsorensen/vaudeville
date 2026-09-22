"""Typed rule models: DecideRule, RewriteRule, the Rule union, and RuleSet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .actions import Action

VALID_TIERS: tuple[str, ...] = ("disabled", "shadow", "log", "warn", "block")

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
    tier: str = "block"
    draft: bool = False
    test_cases: list[DecideTestCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_tier(self) -> "DecideRule":
        if self.tier not in VALID_TIERS:
            raise ValueError(
                f"rule {self.name!r}: invalid tier {self.tier!r}, must be one of {VALID_TIERS}"
            )
        return self

    @model_validator(mode="after")
    def _validate_typesafe_reason(self) -> "DecideRule":
        if self.model and self.model.startswith("typesafe:") and self.reason == "text":
            raise ValueError(
                f"rule {self.name!r}: a typesafe: model cannot declare reason: text "
                "(Jev returns typed values, not free text)"
            )
        return self


_BASH_COMMAND_TARGET = "tool_input.command"


def _target_is_bash_command(target: str, matcher: str | None) -> bool:
    """Return True when `target` resolves to a Bash command's argv, or a
    dotted subpath under it (e.g. `tool_input.command.x`).
    """
    is_command_subpath = target == _BASH_COMMAND_TARGET or target.startswith(
        _BASH_COMMAND_TARGET + "."
    )
    if matcher and "Bash" in matcher.split("|"):
        return is_command_subpath
    if not matcher:
        return is_command_subpath or target.endswith(".command")
    return False


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
    tier: str = "block"
    draft: bool = False

    @model_validator(mode="after")
    def _validate_tier(self) -> "RewriteRule":
        if self.tier not in VALID_TIERS:
            raise ValueError(
                f"rule {self.name!r}: invalid tier {self.tier!r}, must be one of {VALID_TIERS}"
            )
        return self

    @model_validator(mode="after")
    def _validate_target_no_bash(self) -> "RewriteRule":
        for target in self.target:
            if _target_is_bash_command(target, self.matcher):
                raise ValueError(
                    f"rule {self.name!r}: target {target!r} resolves to a Bash command "
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
