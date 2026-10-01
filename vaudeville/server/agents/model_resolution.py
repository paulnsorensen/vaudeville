"""Resolve a decide/rewrite rule's model and build its pydantic-ai agent.

No provider is hard-coded: the default model, the allowed providers, and
each provider's credential reference all come from `UserConfig`. A
missing default, an unlisted provider, or an unavailable credential all
resolve to no model (fail-open allow) rather than raising.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic_ai import Agent
from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers import Provider, infer_provider_class

from vaudeville.rules import DecideRule, RewriteRule
from vaudeville.server.user_config import ProviderConfig, UserConfig

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
    `notice` is set for unavailable credentials; `resolve_model` logs it
    once per provider per process.
    """

    model: Model | str | None
    notice: str | None = None


def resolve_model(
    rule: DecideRule | RewriteRule, config: UserConfig, *, build: bool = True
) -> ModelResolution:
    """Resolve `rule.model` (or `config.default_model`) to a callable model.

    The provider prefix (text before the first `:`) must be a key in
    `config.providers`, and its configured credential source must be usable.
    The model receives that key explicitly, without a provider fallback. With
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
    try:
        api_key = _read_key(provider)
    except (OSError, ValueError, RuntimeError):
        source = "key_file_env" if provider.key_file_env is not None else "key_file"
        api_key = None
        detail = f"cannot read {source}; check its path, permissions, and single UTF-8 key"
    else:
        detail = f"key env var {provider.key_env!r} is not set"
    if not api_key:
        notice = f"vaudeville: provider {provider_name!r} {detail}; allowing"
        if provider_name not in _notified_providers:
            _notified_providers.add(provider_name)
            logger.warning("%s", notice)
        return ModelResolution(model=None, notice=notice)
    if not build:
        return ModelResolution(model=model_name)
    try:
        base_url = str(provider.base_url) if provider.base_url is not None else None
        return ModelResolution(model=_build_model(model_name, api_key, base_url))
    except Exception as exc:
        detail = type(exc).__name__
        logger.warning("vaudeville: cannot build model %r (%s); allowing", model_name, detail)
        return ModelResolution(model=None)


def _read_key(provider: ProviderConfig) -> str | None:
    if provider.key_env is not None:
        return os.environ.get(provider.key_env)
    path_text = provider.key_file
    if provider.key_file_env is not None:
        path_text = os.environ.get(provider.key_file_env)
    if not path_text:
        raise ValueError("missing credential path")
    path = Path(path_text)
    if not (path.is_absolute() or path_text.startswith("~/")):
        raise ValueError("credential path must be absolute or start with ~/")
    path = path.expanduser()
    # Nonblocking open and descriptor validation also reject a FIFO swapped in
    # after a path check. Read at most 64 KiB plus one overflow byte.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("credential file must be regular")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(65537)
    finally:
        os.close(descriptor)
    if len(content) > 65536:
        raise ValueError("credential file is too large")
    key = content.decode("utf-8").strip()
    if not key or len(key.splitlines()) != 1:
        raise ValueError("credential file must contain one key")
    return key


def _build_model(model_name: str, api_key: str, base_url: str | None = None) -> Model:
    def _provider(name: str) -> Provider[Any]:
        provider_cls: Any = infer_provider_class(name)
        kwargs = {"api_key": api_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        provider: Provider[Any] = provider_cls(**kwargs)
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
