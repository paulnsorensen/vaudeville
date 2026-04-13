"""Structured JSONL event logger for classification results.

Writes all classifications to ``events.jsonl`` and violations to
``violations.jsonl`` under ``~/.vaudeville/logs/``.  Uses loguru for
rotation and TTL-based retention.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger as _loguru

from .log_config import LogConfig, load_log_config

_LOGS_DIR = os.path.join(os.path.expanduser("~"), ".vaudeville", "logs")


@dataclass(frozen=True)
class ClassificationEvent:
    rule: str
    verdict: str
    confidence: float
    latency_ms: float
    prompt_chars: int
    reason: str = ""
    input_snippet: str = ""


class EventLogger:
    """Appends JSONL classification events. Pass logs_dir to override default path (useful for tests)."""

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

        # Remove the default stderr sink so loguru JSON doesn't interleave
        # with the daemon's stdlib logging output.
        try:
            _loguru.remove(0)
        except ValueError:
            pass  # already removed by a prior EventLogger in this process

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

    def log_event(self, event: ClassificationEvent) -> None:
        ts = datetime.now(tz=timezone.utc).isoformat()
        common: dict[str, Any] = {
            "ts": ts,
            "rule": event.rule,
            "verdict": event.verdict,
            "confidence": round(event.confidence, 4),
            "latency_ms": round(event.latency_ms, 1),
            "prompt_chars": event.prompt_chars,
        }

        self._logger.bind(_sink="events").info(json.dumps(common, default=str))

        if event.verdict == "violation":
            violation = {
                **common,
                "reason": event.reason,
                "input_snippet": event.input_snippet[:500],
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
