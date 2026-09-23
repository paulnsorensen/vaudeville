"""Build and run the pydantic-ai rewrite agent for a RewriteRule."""

from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models import Model

from vaudeville.rules import RewriteRule
from vaudeville.server.user_config import UserConfig

from .delimit import delimit_hook_text
from .model_resolution import build_agent, resolve_model

logger = logging.getLogger(__name__)

REWRITE_LENGTH_CAP = 2000


def build_rewrite_agent(rule: RewriteRule, model: Model | str) -> Agent[None, str]:
    """Build the pydantic-ai agent for `rule`, producing a plain rewritten string."""
    return build_agent(rule.prompt, model, str)


def rewrite(
    rule: RewriteRule,
    config: UserConfig,
    text: str,
    *,
    model_override: Model | None = None,
) -> str | None:
    """Resolve `rule`'s model and rewrite `text`, or fail open to None.

    None means no rewrite: no model resolves (unlisted provider, unset key)
    or the output exceeds the length cap. `model_override` substitutes for
    the resolved model only when resolution permits a call, as in `decide`.
    """
    resolution = resolve_model(rule, config, build=model_override is None)
    if resolution.model is None:
        return None
    model = model_override if model_override is not None else resolution.model
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
