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

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_PATH: str = str(Path.home() / ".vaudeville" / "config")


class ProviderConfig(BaseModel):
    """One allowed model provider with exactly one credential reference."""

    model_config = ConfigDict(extra="forbid")

    key_env: str | None = None
    key_file: str | None = None
    key_file_env: str | None = None
    base_url: AnyHttpUrl | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and (value.username is not None or value.password is not None):
            raise ValueError("base_url must not contain userinfo")
        return value

    @model_validator(mode="after")
    def _validate_key_source(self) -> ProviderConfig:
        sources = (self.key_env, self.key_file, self.key_file_env)
        if sum(source is not None for source in sources) != 1:
            raise ValueError("configure exactly one of key_env, key_file, or key_file_env")
        if any(source is not None and not source.strip() for source in sources):
            raise ValueError("credential references must not be empty")
        if self.key_file is not None and not (
            Path(self.key_file).is_absolute() or self.key_file.startswith("~/")
        ):
            raise ValueError("key_file must be an absolute path or start with ~/")
        return self


class UserConfig(BaseModel):
    """Parsed ~/.vaudeville/config: default model, providers, commands."""

    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    commands: dict[str, Annotated[list[str], Field(min_length=1)]] = Field(default_factory=dict)


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
