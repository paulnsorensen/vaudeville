"""Build and run the pydantic-ai rewrite agent for a RewriteRule."""

from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models import Model

from vaudeville.rules import RewriteRule

from .delimit import DATA_INSTRUCTION, delimit_hook_text
from .model_resolution import MODEL_REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

REWRITE_LENGTH_CAP = 2000


def build_rewrite_agent(rule: RewriteRule, model: Model | str) -> Agent[None, str]:
    """Build the pydantic-ai agent for `rule`, producing a plain rewritten string."""
    system_prompt = f"{rule.prompt}\n\n{DATA_INSTRUCTION}"
    return Agent(
        model,
        output_type=str,
        system_prompt=system_prompt,
        model_settings={
            "temperature": 0.0,
            "timeout": MODEL_REQUEST_TIMEOUT_SECONDS,
        },
    )


def rewrite(rule: RewriteRule, model: Model | str, text: str) -> str | None:
    """Run the rewrite agent on `text`, discarding an output over the length cap."""
    agent = build_rewrite_agent(rule, model)
    result = agent.run_sync(delimit_hook_text(text))
    output = result.output
    if len(output) > REWRITE_LENGTH_CAP:
        logger.warning(
            "rewrite output for rule %r exceeded length cap (%d > %d chars); discarded",
            rule.name,
            len(output),
            REWRITE_LENGTH_CAP,
        )
        return None
    return output
