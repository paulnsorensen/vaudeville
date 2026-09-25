"""Tests for the ~/.vaudeville/config loader (AC-13, F-9, F-10)."""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from vaudeville.server.user_config import ProviderConfig, UserConfig, load_user_config


class TestLoadUserConfig:
    def test_missing_file_returns_empty_config(self, tmp_path: Path) -> None:
        config = load_user_config(tmp_path / "does-not-exist")

        assert config == UserConfig()
        assert config.default_model is None
        assert config.providers == {}
        assert config.commands == {}

    def test_loads_default_model_providers_and_commands(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config"
        config_path.write_text(
            "default_model: anthropic:claude-haiku-4-5\n"
            "providers:\n"
            "  anthropic:\n"
            "    key_env: ANTHROPIC_API_KEY\n"
            "commands:\n"
            "  notify-slack: [/usr/local/bin/notify-slack]\n"
        )

        config = load_user_config(config_path)

        assert config.default_model == "anthropic:claude-haiku-4-5"
        assert config.providers == {"anthropic": ProviderConfig(key_env="ANTHROPIC_API_KEY")}
        assert config.commands == {"notify-slack": ["/usr/local/bin/notify-slack"]}

    def test_empty_file_returns_empty_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config"
        config_path.write_text("")

        config = load_user_config(config_path)

        assert config == UserConfig()


class TestCommandArgv:
    def test_empty_command_argv_is_rejected(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            UserConfig.model_validate({"commands": {"empty": []}})
