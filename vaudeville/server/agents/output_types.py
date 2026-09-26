"""Build a decide rule's pydantic output type from its outcomes and reasons."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model

from vaudeville.rules import DecideRule

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def _class_name(rule_name: str) -> str:
    slug = _NON_ALNUM.sub("_", rule_name).strip("_") or "Rule"
    return f"{slug}Output"


def build_decide_output_type(rule: DecideRule) -> type[BaseModel]:
    """Build a pydantic model for a decide agent's output.

    `outcome` is a Literal over `rule.outcomes`. `reason` is a Literal over
    the `rule.reasons` bucket ids when the rule declares them, a free-text
    string when the rule declares `reason: text`, otherwise the field is
    absent. There is no `confidence` field: `decide` reads confidence from
    the model response's `provider_details`, not from a self-report.
    """
    outcome_type: Any = Literal[tuple(rule.outcomes)]
    fields: dict[str, Any] = {
        "outcome": (outcome_type, ...),
    }
    if rule.reasons:
        reason_type: Any = Literal[tuple(rule.reasons.keys())]
        fields["reason"] = (reason_type | None, None)
    elif rule.reason == "text":
        fields["reason"] = (str | None, None)
    model: type[BaseModel] = create_model(
        _class_name(rule.name),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return model
