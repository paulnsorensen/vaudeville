"""Action vocabulary for decide rule outcomes.

Actions are the five-tier vocabulary that PR #95 introduced, expanded to the
full ten-action set an `on:` outcome map can select from.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

ActionName = Literal[
    "allow",
    "log",
    "warn",
    "block",
    "feedback",
    "rewrite",
    "escalate",
    "ask",
    "add-context",
    "run",
]

ACTION_NAMES: tuple[ActionName, ...] = get_args(ActionName)


class Action(BaseModel):
    """One action, optionally carrying a parameter.

    A plain string (for example ``block``) expands to a parameterless
    action. A mapping (for example ``{action: rewrite, rule: fix-it}``)
    carries a parameter: the rewrite or escalate rule name, the named `run`
    command, or the `add-context` text.
    """

    model_config = ConfigDict(extra="forbid")

    action: ActionName
    rule: str | None = None
    command: str | None = None
    text: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _expand_shorthand(cls, data: object) -> object:
        if isinstance(data, str):
            data = {"action": data}
        if isinstance(data, dict):
            _require_parameter(data)
        return data


def _require_parameter(data: dict[object, object]) -> None:
    """Reject a configured action that cannot run without its parameter.

    Only raw config input is checked; an already-built `Action` (for example
    a resolved action a harness renders) passes through unchanged.
    """
    action = data.get("action")
    if action in ("rewrite", "escalate") and not data.get("rule"):
        raise ValueError(f"action {action!r} requires a `rule` name")
    if action == "run" and not data.get("command"):
        raise ValueError("action 'run' requires a `command` name")
