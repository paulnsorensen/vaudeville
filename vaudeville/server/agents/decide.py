"""Build and run the pydantic-ai decide agent for a DecideRule."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

from vaudeville.rules import DecideRule
from vaudeville.server.user_config import UserConfig

from .delimit import DATA_INSTRUCTION, delimit_hook_text
from .model_resolution import resolve_model
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


def build_decide_agent(rule: DecideRule, model: Model | str) -> Agent[None, Any]:
    """Build the pydantic-ai agent for `rule`, its output type built from `outcomes`."""
    output_type = build_decide_output_type(rule)
    system_prompt = f"{rule.prompt}\n\n{DATA_INSTRUCTION}"
    return Agent(
        model,
        output_type=output_type,
        system_prompt=system_prompt,
        model_settings={"temperature": 0.0},
    )


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
    resolution = resolve_model(rule, config)
    if resolution.notice:
        print(resolution.notice, file=sys.stderr)
    if resolution.model is None:
        return ALLOW
    model = model_override if model_override is not None else resolution.model
    agent = build_decide_agent(rule, model)
    result = agent.run_sync(delimit_hook_text(text))
    output = result.output
    return DecideResult(
        outcome=output.outcome,
        reason=getattr(output, "reason", None),
        confidence=getattr(output, "confidence", None),
    )
