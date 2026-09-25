"""Log configuration loader for the observability layer.

Reads ``~/.vaudeville/logs/config.yaml`` for retention and size settings.
Creates the file with defaults when absent.
"""

from __future__ import annotations

import contextlib
import pathlib
from dataclasses import dataclass

import yaml

DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_SIZE_MB = 10

_LOGS_DIR = str(pathlib.Path.home() / ".vaudeville" / "logs")
_CONFIG_PATH = str(pathlib.Path(_LOGS_DIR) / "config.yaml")


@dataclass(frozen=True)
class LogConfig:
    retention_days: int = DEFAULT_RETENTION_DAYS
    max_size_mb: int = DEFAULT_MAX_SIZE_MB


def _write_defaults(path: str) -> None:
    pathlib.Path(path).parent.mkdir(exist_ok=True, parents=True)
    with pathlib.Path(path).open("w") as f:
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
    if not pathlib.Path(config_path).exists():
        with contextlib.suppress(OSError):
            _write_defaults(config_path)
        return LogConfig()

    try:
        with pathlib.Path(config_path).open() as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return LogConfig()

    if not isinstance(data, dict):
        return LogConfig()

    try:
        return LogConfig(
            retention_days=int(data.get("retention_days", DEFAULT_RETENTION_DAYS)),
            max_size_mb=int(data.get("max_size_mb", DEFAULT_MAX_SIZE_MB)),
        )
    except (TypeError, ValueError):
        return LogConfig()
