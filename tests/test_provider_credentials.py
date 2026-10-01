"""Credential references stay separate from secret values."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.models.typesafe import TypeSafeModel

from vaudeville.rules import DecideRule, parse_rule
from vaudeville.server.agents import model_resolution
from vaudeville.server.agents.model_resolution import resolve_model
from vaudeville.server.effects.run_command import _child_env
from vaudeville.server.user_config import ProviderConfig, UserConfig


def _config(source: dict[str, str]) -> UserConfig:
    return UserConfig.model_validate(
        {"default_model": "typesafe:jev-1.13.0", "providers": {"typesafe": source}}
    )


def _rule() -> DecideRule:
    rule = parse_rule(
        {
            "name": "credential-test",
            "type": "decide",
            "event": "Stop",
            "on": {"clean": "allow"},
            "prompt": "Check.",
            "outcomes": ["clean"],
        }
    )
    assert isinstance(rule, DecideRule)
    return rule


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"key_env": ""},
        {"key_env": "  "},
        {"key_file": ""},
        {"key_file_env": "\t"},
        {"key_file": "relative/key"},
        {"key_file": "$HOME/key"},
        {"api_key": "literal-secret"},
        {"key_env": "KEY", "key_file": "/key"},
        {"key_env": "KEY", "key_file_env": "KEY_PATH"},
        {"key_file": "/key", "key_file_env": "KEY_PATH"},
    ],
)
def test_rejects_invalid_source(source: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(source)


@pytest.mark.parametrize("field", ["key_file", "key_file_env"])
def test_file_key_builds_without_environment_mutation(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "key with spaces"
    path.write_text(" \tconfigured-file-key\n", encoding="utf-8")
    monkeypatch.setenv("KEY_PATH", str(path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "wrong-default")
    config = _config({field: str(path) if field == "key_file" else "KEY_PATH"})
    before = dict(os.environ)
    result = resolve_model(_rule(), config)
    assert isinstance(result.model, TypeSafeModel)
    assert result.model.client._config.api_key == "configured-file-key"
    assert result.notice is None
    assert dict(os.environ) == before
    assert "configured-file-key" not in config.model_dump_json()


def test_home_path_and_build_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "key").write_text("fake-key")
    config = _config({"key_file": "~/key"})
    assert resolve_model(_rule(), config, build=False).model == "typesafe:jev-1.13.0"


@pytest.mark.parametrize(
    "contents", [b"", b" \n", b"secret\nsecond", b"secret\rsecond", b"secret\xff", b"s" * 65537]
)
def test_invalid_file_fails_open_without_secret(
    contents: bytes,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "key"
    path.write_bytes(contents)
    monkeypatch.setattr(model_resolution, "_notified_providers", set())
    config = _config({"key_file": str(path)})
    with caplog.at_level(logging.WARNING):
        first = resolve_model(_rule(), config, build=False)
        second = resolve_model(_rule(), config)
    assert first.model is second.model is None
    assert first.notice and "key_file" in first.notice
    assert len(caplog.records) == 1
    assert "secret" not in caplog.text
    assert "second" not in caplog.text


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_unusable_file_fails_open(kind: str, tmp_path: Path) -> None:
    path = tmp_path / "key"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    config = _config({"key_file": str(path)})
    result = resolve_model(_rule(), config, build=False)
    assert result.model is None
    assert result.notice and "key_file" in result.notice


@pytest.mark.parametrize("value", [None, "", "relative/key", "$HOME/key"])
def test_invalid_path_environment_fails_open(
    value: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KEY_PATH", raising=False)
    if value is not None:
        monkeypatch.setenv("KEY_PATH", value)
    config = _config({"key_file_env": "KEY_PATH"})
    assert resolve_model(_rule(), config, build=False).model is None


def test_named_user_environment_path_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pwd

    user = pwd.getpwuid(os.getuid())
    path = tmp_path / "key"
    path.write_text("fake-key")
    relative = os.path.relpath(path, user.pw_dir)
    reference = f"~{user.pw_name}/{relative}"
    assert Path(reference).expanduser().resolve() == path
    monkeypatch.setenv("KEY_PATH", reference)
    result = resolve_model(_rule(), _config({"key_file_env": "KEY_PATH"}))
    assert result.model is None
    assert result.notice and "key_file_env" in result.notice


def test_file_config_does_not_read_secret_and_child_keeps_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEY_PATH", str(tmp_path / "missing"))
    monkeypatch.setenv("CUSTOM_CREDENTIAL", "secret")
    config = _config({"key_file_env": "KEY_PATH"})
    config.providers["other"] = ProviderConfig(key_env="CUSTOM_CREDENTIAL")
    child = _child_env(config)
    assert child["KEY_PATH"] == str(tmp_path / "missing")
    assert "CUSTOM_CREDENTIAL" not in child


def test_openrouter_file_key_builds_without_network(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_text("fake-openrouter-key")
    config = UserConfig.model_validate(
        {
            "default_model": "openrouter:anthropic/claude-haiku-4.5",
            "providers": {"openrouter": {"key_file": str(path)}},
        }
    )
    result = resolve_model(_rule(), config)
    assert result.model is not None
    assert type(result.model).__name__ == "OpenRouterModel"
    assert result.notice is None


def test_unreadable_file_fails_open(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_text("private-key")
    path.chmod(0)
    try:
        result = resolve_model(_rule(), _config({"key_file": str(path)}))
    finally:
        path.chmod(0o600)
    assert result.model is None
    assert result.notice and "permissions" in result.notice
    assert "private-key" not in result.notice


def test_maximum_file_size_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_bytes(b"k" * 65536)
    result = resolve_model(_rule(), _config({"key_file": str(path)}), build=False)
    assert result.model == "typesafe:jev-1.13.0"


def test_import_error_does_not_expose_file_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "key"
    path.write_text("private-key")

    def fail(*args: object, **kwargs: object) -> None:
        raise ImportError("private-key")

    monkeypatch.setattr(model_resolution, "infer_model", fail)
    result = resolve_model(_rule(), _config({"key_file": str(path)}))
    assert result.model is None
    assert "ImportError" in caplog.text
    assert "private-key" not in caplog.text


@pytest.mark.parametrize("base_url", ["https://openrouter.ai/api", "http://localhost:8080/api"])
def test_provider_accepts_http_endpoint(base_url: str) -> None:
    config = ProviderConfig.model_validate({"key_env": "KEY", "base_url": base_url})
    assert str(config.base_url) == base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "relative/api",
        "ftp://example.com/api",
        "https://",
        "https://user:secret@example.com/api",
    ],
)
def test_provider_rejects_invalid_endpoint(base_url: str) -> None:
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate({"key_env": "KEY", "base_url": base_url})


def test_jev_endpoint_overrides_ambient_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "key"
    path.write_text("private-openrouter-key")
    monkeypatch.setenv("VAUDEVILLE_API_KEY_FILE", str(path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "wrong-default")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://wrong.example/api")
    config = UserConfig.model_validate(
        {
            "default_model": "typesafe:jev-1.13",
            "providers": {
                "typesafe": {
                    "key_file_env": "VAUDEVILLE_API_KEY_FILE",
                    "base_url": "https://openrouter.ai/api",
                }
            },
        }
    )
    before = dict(os.environ)
    result = resolve_model(_rule(), config)
    assert isinstance(result.model, TypeSafeModel)
    assert result.model.model_name == "jev-1.13"
    assert result.model.client._config.base_url == "https://openrouter.ai/api"
    assert result.model.client._config.api_key == "private-openrouter-key"
    assert result.notice is None
    assert dict(os.environ) == before
    assert "private-openrouter-key" not in config.model_dump_json()
    assert "private-openrouter-key" not in caplog.text


def test_unsupported_endpoint_fails_open_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "key"
    path.write_text("private-key")
    calls: list[dict[str, str]] = []

    def unsupported_provider(**kwargs: str) -> None:
        calls.append(kwargs)
        raise TypeError("unsupported endpoint private-key")

    monkeypatch.setattr(
        model_resolution, "infer_provider_class", lambda name: unsupported_provider
    )
    config = _config({"key_file": str(path), "base_url": "https://openrouter.ai/api"})
    result = resolve_model(_rule(), config)
    assert result.model is None
    assert calls == [{"api_key": "private-key", "base_url": "https://openrouter.ai/api"}]
    assert "TypeError" in caplog.text
    assert "private-key" not in caplog.text
