"""Structured JSONL event logger for classification results.

Writes all classifications to ``events.jsonl`` and violations to
``violations.jsonl`` under ``~/.vaudeville/logs/``.  Uses loguru for
rotation and TTL-based retention.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger as _loguru

from .log_config import LogConfig, load_log_config

_LOGS_DIR = os.path.join(os.path.expanduser("~"), ".vaudeville", "logs")


class EventLogger:
    """Append structured JSONL events for classifications.

    Parameters
    ----------
    config:
        Log rotation/retention settings.  Loaded from disk when *None*.
    logs_dir:
        Override the default ``~/.vaudeville/logs/`` directory (useful
        for testing).
    """

    def __init__(
        self,
        config: LogConfig | None = None,
        logs_dir: str = _LOGS_DIR,
    ) -> None:
        if config is None:
            config = load_log_config()
        self._config = config
        self._logs_dir = logs_dir
        os.makedirs(logs_dir, exist_ok=True)

        self._logger = _loguru.bind()
        self._events_id: int | None = None
        self._violations_id: int | None = None
        self._configure_sinks()

    def _configure_sinks(self) -> None:
        rotation = f"{self._config.max_size_mb} MB"
        retention = timedelta(days=self._config.retention_days)

        events_path = os.path.join(self._logs_dir, "events.jsonl")
        violations_path = os.path.join(self._logs_dir, "violations.jsonl")

        # Use {message} as format — we pass pre-serialized JSON as
        # the message, so loguru writes exactly one JSONL line per event.
        self._events_id = self._logger.add(
            events_path,
            format="{message}",
            rotation=rotation,
            retention=retention,
            level="INFO",
            filter=lambda r: r["extra"].get("_sink") == "events",
        )
        self._violations_id = self._logger.add(
            violations_path,
            format="{message}",
            rotation=rotation,
            retention=retention,
            level="INFO",
            filter=lambda r: r["extra"].get("_sink") == "violations",
        )

    def log_event(
        self,
        rule: str,
        verdict: str,
        confidence: float,
        latency_ms: float,
        prompt_chars: int,
        reason: str = "",
        input_snippet: str = "",
    ) -> None:
        """Record a classification event."""
        ts = datetime.now(tz=timezone.utc).isoformat()
        common: dict[str, Any] = {
            "ts": ts,
            "rule": rule,
            "verdict": verdict,
            "confidence": round(confidence, 4),
            "latency_ms": round(latency_ms, 1),
            "prompt_chars": prompt_chars,
        }

        self._logger.bind(_sink="events").info(json.dumps(common, default=str))

        if verdict == "violation":
            violation = {
                **common,
                "reason": reason,
                "input_snippet": input_snippet[:500],
            }
            self._logger.bind(_sink="violations").info(
                json.dumps(violation, default=str)
            )

    def close(self) -> None:
        """Remove sinks added by this logger."""
        if self._events_id is not None:
            self._logger.remove(self._events_id)
            self._events_id = None
        if self._violations_id is not None:
            self._logger.remove(self._violations_id)
            self._violations_id = None
