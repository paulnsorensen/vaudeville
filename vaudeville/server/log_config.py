"""Log configuration loader for the observability layer.

Reads ``~/.vaudeville/logs/config.yaml`` for retention and size settings.
Creates the file with defaults when absent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_SIZE_MB = 10

_LOGS_DIR = os.path.join(os.path.expanduser("~"), ".vaudeville", "logs")
_CONFIG_PATH = os.path.join(_LOGS_DIR, "config.yaml")


@dataclass(frozen=True)
class LogConfig:
    retention_days: int = DEFAULT_RETENTION_DAYS
    max_size_mb: int = DEFAULT_MAX_SIZE_MB


def _write_defaults(path: str) -> None:
    """Write default config.yaml to *path*, creating parent dirs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(
            {
                "retention_days": DEFAULT_RETENTION_DAYS,
                "max_size_mb": DEFAULT_MAX_SIZE_MB,
            },
            f,
        )


def load_log_config(config_path: str = _CONFIG_PATH) -> LogConfig:
    """Load log config from *config_path*, creating defaults if absent.

    Malformed or unreadable files fall back to defaults without raising.
    """
    if not os.path.exists(config_path):
        try:
            _write_defaults(config_path)
        except OSError:
            pass
        return LogConfig()

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return LogConfig()

    if not isinstance(data, dict):
        return LogConfig()

    return LogConfig(
        retention_days=int(data.get("retention_days", DEFAULT_RETENTION_DAYS)),
        max_size_mb=int(data.get("max_size_mb", DEFAULT_MAX_SIZE_MB)),
    )
