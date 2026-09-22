"""Resolve a decide/rewrite rule's model string against the user config.

No provider is hard-coded: the default model, the allowed providers, and
each provider's key environment variable all come from `UserConfig`. A
missing default, an unlisted provider, or an unset key variable all
resolve to no model (fail-open allow) rather than raising.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_ai.models import Model
from pydantic_ai.models.typesafe import TypeSafeModel

from vaudeville.rules import DecideRule, RewriteRule
from vaudeville.server.user_config import UserConfig

TYPESAFE_PROVIDER = "typesafe"


@dataclass(frozen=True)
class ModelResolution:
    """Result of resolving a rule's model string.

    `model` is None when no call should be made (missing model name,
    unlisted provider, or unset key). `notice` is set only for the
    unset-key case and is meant for a single stderr line.
    """

    model: Model | str | None
    notice: str | None = None


def resolve_model(
    rule: DecideRule | RewriteRule, config: UserConfig
) -> ModelResolution:
    """Resolve `rule.model` (or `config.default_model`) to a callable model.

    The provider prefix (text before the first `:`) must be a key in
    `config.providers`, and that provider's `key_env` must be set in the
    environment. A `typesafe:` model builds a `TypeSafeModel`; any other
    provider is passed through as a pydantic-ai `provider:model` string.
    """
    model_name = rule.model or config.default_model
    if not model_name:
        return ModelResolution(model=None)
    provider_name, sep, rest = model_name.partition(":")
    if not sep or not rest:
        return ModelResolution(model=None)
    provider = config.providers.get(provider_name)
    if provider is None:
        return ModelResolution(model=None)
    if not os.environ.get(provider.key_env):
        notice = (
            f"vaudeville: provider {provider_name!r} key env var "
            f"{provider.key_env!r} is not set; allowing"
        )
        return ModelResolution(model=None, notice=notice)
    if provider_name == TYPESAFE_PROVIDER:
        return ModelResolution(model=TypeSafeModel(rest))
    return ModelResolution(model=model_name)
