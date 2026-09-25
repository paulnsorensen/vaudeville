"""User-level config loader for ~/.vaudeville/config.

The file is YAML at the literal path ``~/.vaudeville/config`` (no
extension). A missing file yields an empty config, which is fail-open:
an empty config names no default model, no allowed provider, and no
runnable command, so every decide/rewrite/run gated by it falls back
to allow.

Example file::

    default_model: anthropic:claude-haiku-4-5
    providers:
      anthropic:
        key_env: ANTHROPIC_API_KEY
    commands:
      notify-slack: ["/usr/local/bin/notify-slack"]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONFIG_PATH: str = os.path.join(os.path.expanduser("~"), ".vaudeville", "config")


class ProviderConfig(BaseModel):
    """One allowed model provider: the environment variable holding its key."""

    model_config = ConfigDict(extra="forbid")

    key_env: str


class UserConfig(BaseModel):
    """Parsed ~/.vaudeville/config: default model, providers, commands."""

    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    commands: dict[str, Annotated[list[str], Field(min_length=1)]] = Field(
        default_factory=dict
    )


def load_user_config(path: str | Path | None = None) -> UserConfig:
    """Load and validate the user config, or return an empty one.

    A missing file returns an empty UserConfig (fail-open); the caller
    is not expected to distinguish "no config" from "empty config".
    """
    config_path = Path(path) if path is not None else Path(CONFIG_PATH)
    if not config_path.is_file():
        return UserConfig()
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not data:
        return UserConfig()
    return UserConfig.model_validate(data)
