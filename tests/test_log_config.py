"""Tests for vaudeville.server.log_config."""

from __future__ import annotations

import yaml

from vaudeville.server.log_config import (
    DEFAULT_MAX_SIZE_MB,
    DEFAULT_RETENTION_DAYS,
    LogConfig,
    load_log_config,
)


def test_defaults(tmp_path: object) -> None:
    """LogConfig() returns expected defaults."""
    cfg = LogConfig()
    assert cfg.retention_days == DEFAULT_RETENTION_DAYS
    assert cfg.max_size_mb == DEFAULT_MAX_SIZE_MB


def test_load_creates_file_when_absent(tmp_path: object) -> None:
    """First call creates config.yaml with defaults."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "logs" / "config.yaml"
    cfg = load_log_config(str(path))

    assert cfg == LogConfig()
    assert path.exists()

    data = yaml.safe_load(path.read_text())
    assert data["retention_days"] == DEFAULT_RETENTION_DAYS
    assert data["max_size_mb"] == DEFAULT_MAX_SIZE_MB


def test_load_reads_custom_values(tmp_path: object) -> None:
    """Reads custom retention and size from YAML."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "config.yaml"
    path.write_text(yaml.safe_dump({"retention_days": 30, "max_size_mb": 50}))

    cfg = load_log_config(str(path))
    assert cfg.retention_days == 30
    assert cfg.max_size_mb == 50


def test_load_falls_back_on_malformed_yaml(tmp_path: object) -> None:
    """Malformed YAML returns defaults."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "config.yaml"
    path.write_text("{{{{not yaml")

    cfg = load_log_config(str(path))
    assert cfg == LogConfig()


def test_load_falls_back_on_non_dict(tmp_path: object) -> None:
    """YAML that parses to a non-dict returns defaults."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "config.yaml"
    path.write_text("- just\n- a\n- list\n")

    cfg = load_log_config(str(path))
    assert cfg == LogConfig()


def test_load_falls_back_on_missing_keys(tmp_path: object) -> None:
    """Missing keys use defaults."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "config.yaml"
    path.write_text(yaml.safe_dump({"retention_days": 14}))

    cfg = load_log_config(str(path))
    assert cfg.retention_days == 14
    assert cfg.max_size_mb == DEFAULT_MAX_SIZE_MB


def test_load_unreadable_file(tmp_path: object) -> None:
    """Unreadable file returns defaults without raising."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "config.yaml"
    path.write_text("retention_days: 5")
    path.chmod(0o000)

    try:
        cfg = load_log_config(str(path))
        assert cfg == LogConfig()
    finally:
        path.chmod(0o644)


def test_load_invalid_value_types(tmp_path: object) -> None:
    """Non-numeric values in config fall back to defaults."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "config.yaml"
    path.write_text(yaml.safe_dump({"retention_days": "abc", "max_size_mb": "xyz"}))

    cfg = load_log_config(str(path))
    assert cfg == LogConfig()


def test_load_write_defaults_oserror(tmp_path: object, monkeypatch: object) -> None:
    """OSError during default file creation returns defaults gracefully."""
    import pathlib
    from unittest.mock import patch

    path = pathlib.Path(str(tmp_path)) / "nonexistent" / "deep" / "config.yaml"

    with patch(
        "vaudeville.server.log_config._write_defaults", side_effect=OSError("no")
    ):
        cfg = load_log_config(str(path))

    assert cfg == LogConfig()
