"""Resolve a decide/rewrite rule's model and build its pydantic-ai agent.

No provider is hard-coded: the default model, the allowed providers, and
each provider's key environment variable all come from `UserConfig`. A
missing default, an unlisted provider, or an unset key variable all
resolve to no model (fail-open allow) rather than raising.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic_ai import Agent
from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers import Provider, infer_provider_class

from vaudeville.rules import DecideRule, RewriteRule
from vaudeville.server.user_config import UserConfig

from .delimit import DATA_INSTRUCTION

logger = logging.getLogger(__name__)

# Per-request model timeout; matches the hook pipeline's request deadline
# so a stalled provider never holds a decide worker past it.
MODEL_REQUEST_TIMEOUT_SECONDS = 6.0

# Providers whose missing-key notice this process already logged.
_notified_providers: set[str] = set()

T = TypeVar("T")


@dataclass(frozen=True)
class ModelResolution:
    """Result of resolving a rule's model string.

    `model` is None when no call should be made (missing model name,
    unlisted provider, unset key, or a model that cannot be built).
    `notice` is set only for the unset-key case; `resolve_model` logs it
    once per provider per process.
    """

    model: Model | str | None
    notice: str | None = None


def resolve_model(
    rule: DecideRule | RewriteRule, config: UserConfig, *, build: bool = True
) -> ModelResolution:
    """Resolve `rule.model` (or `config.default_model`) to a callable model.

    The provider prefix (text before the first `:`) must be a key in
    `config.providers`, and that provider's `key_env` must be set in the
    environment. The model is built with the key read from that `key_env`,
    so pydantic-ai never falls back to its own default variable. With
    `build=False` only the gate runs and the `provider:model` string is
    returned, for callers that substitute their own model.
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
    api_key = os.environ.get(provider.key_env)
    if not api_key:
        notice = (
            f"vaudeville: provider {provider_name!r} key env var "
            f"{provider.key_env!r} is not set; allowing"
        )
        if provider_name not in _notified_providers:
            _notified_providers.add(provider_name)
            logger.warning("%s", notice)
        return ModelResolution(model=None, notice=notice)
    if not build:
        return ModelResolution(model=model_name)
    try:
        return ModelResolution(model=_build_model(model_name, api_key))
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ImportError) else type(exc).__name__
        logger.warning("vaudeville: cannot build model %r (%s); allowing", model_name, detail)
        return ModelResolution(model=None)


def _build_model(model_name: str, api_key: str) -> Model:
    def _provider(name: str) -> Provider[Any]:
        provider_cls: Any = infer_provider_class(name)
        provider: Provider[Any] = provider_cls(api_key=api_key)
        return provider

    return infer_model(model_name, provider_factory=_provider)


def build_agent(prompt: str, model: Model | str, output_type: type[T]) -> Agent[None, T]:
    """Build an agent with the data instruction, temperature 0.0, and the request timeout."""
    return Agent(
        model,
        output_type=output_type,
        system_prompt=f"{prompt}\n\n{DATA_INSTRUCTION}",
        model_settings={
            "temperature": 0.0,
            "timeout": MODEL_REQUEST_TIMEOUT_SECONDS,
        },
    )
