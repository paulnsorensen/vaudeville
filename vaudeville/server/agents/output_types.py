"""Build a decide rule's pydantic output type from its outcomes and reasons."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from vaudeville.rules import DecideRule

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def _class_name(rule_name: str) -> str:
    slug = _NON_ALNUM.sub("_", rule_name).strip("_") or "Rule"
    return f"{slug}Output"


def build_decide_output_type(rule: DecideRule) -> type[BaseModel]:
    """Build a pydantic model for a decide agent's output.

    `outcome` is a Literal over `rule.outcomes`. `reason` is a Literal over
    the `rule.reasons` bucket ids when the rule declares them, otherwise
    the field is absent. `confidence`, when present, is bounded to [0, 1].
    """
    outcome_type: Any = Literal[tuple(rule.outcomes)]
    fields: dict[str, Any] = {
        "outcome": (outcome_type, ...),
        "confidence": (float | None, Field(default=None, ge=0.0, le=1.0)),
    }
    if rule.reasons:
        reason_type: Any = Literal[tuple(rule.reasons.keys())]
        fields["reason"] = (reason_type | None, None)
    model: type[BaseModel] = create_model(
        _class_name(rule.name),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return model
