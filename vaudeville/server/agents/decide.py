"""Build and run the pydantic-ai decide agent for a DecideRule."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

from vaudeville.rules import DecideRule
from vaudeville.server.user_config import UserConfig

from .delimit import delimit_hook_text
from .model_resolution import build_agent, resolve_model
from .output_types import build_decide_output_type

# Suppress the first-run startup banner pydantic-ai prints to stderr; it
# would otherwise count as an unrelated stderr line alongside notices.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@dataclass(frozen=True)
class DecideResult:
    """Outcome of a decide run. `outcome is None` means fail-open allow."""

    outcome: str | None
    reason: str | None = None
    confidence: float | None = None


ALLOW = DecideResult(outcome=None)


def _valid_confidence(value: object) -> float | None:
    """Return `value` as a float in [0, 1], or None; never raises."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and 0.0 <= value <= 1.0:
        return float(value)
    return None


def _confidence_from_provider_details(
    provider_details: dict[str, Any] | None, outcome: str
) -> float | None:
    """Read `outcome`'s confidence from `provider_details`, fail-open.

    Tries `probabilities['outcome'][outcome]` first, then
    `confidence['outcome']`, else None. A malformed `provider_details`
    (wrong shape, missing keys, or an out-of-range value) never raises.
    """
    if not isinstance(provider_details, dict):
        return None
    probabilities = provider_details.get("probabilities")
    if isinstance(probabilities, dict):
        outcome_probabilities = probabilities.get("outcome")
        if isinstance(outcome_probabilities, dict):
            value = _valid_confidence(outcome_probabilities.get(outcome))
            if value is not None:
                return value
    confidence = provider_details.get("confidence")
    if isinstance(confidence, dict):
        value = _valid_confidence(confidence.get("outcome"))
        if value is not None:
            return value
    return None


def build_decide_agent(rule: DecideRule, model: Model | str) -> Agent[None, Any]:
    """Build the pydantic-ai agent for `rule`, its output type built from `outcomes`."""
    return build_agent(rule.prompt, model, build_decide_output_type(rule))


def decide(
    rule: DecideRule,
    config: UserConfig,
    text: str,
    *,
    model_override: Model | None = None,
) -> DecideResult:
    """Resolve `rule`'s model and run the decide agent, or fail open to allow.

    `model_override` substitutes for the resolved model (for tests, a
    `FunctionModel`/`TestModel`) but only when resolution actually permits
    a call; an unlisted provider or an unset key never reaches it.
    """
    resolution = resolve_model(rule, config, build=model_override is None)
    if resolution.model is None:
        return ALLOW
    model = model_override if model_override is not None else resolution.model
    agent = build_decide_agent(rule, model)
    result = agent.run_sync(delimit_hook_text(text))
    output = result.output
    confidence = _confidence_from_provider_details(
        result.response.provider_details, output.outcome
    )
    return DecideResult(
        outcome=output.outcome,
        reason=getattr(output, "reason", None),
        confidence=confidence,
    )
