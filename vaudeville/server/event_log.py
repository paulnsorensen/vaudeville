"""Structured JSONL event logger for classification results.

Writes all classifications to ``events.jsonl`` and violations to
``violations.jsonl`` under ``~/.vaudeville/logs/``.  Uses loguru for
rotation and TTL-based retention.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger as _loguru

from .log_config import LogConfig, load_log_config

_LOGS_DIR = str(pathlib.Path.home() / ".vaudeville" / "logs")
# Keep event rows lightweight for fast tail/read operations in watch mode.
_MAX_SNIPPET_LOG_CHARS = 500

# Actions that gate the session; only these route to violations.jsonl (F23).
_BLOCKING_ACTIONS = frozenset({"block", "ask"})


@dataclass(frozen=True)
class ClassificationEvent:
    rule: str
    verdict: str
    confidence: float | None
    latency_ms: float
    prompt_chars: int
    reason: str = ""
    input_snippet: str = ""
    tier: str = "block"
    outcome: str | None = None
    action: str | None = None
    model: str | None = None
    downgrade: str | None = None
    kind: str | None = None


class EventLogger:
    """Appends JSONL classification events.

    Pass logs_dir to override the default path (useful for tests).
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
        pathlib.Path(logs_dir).mkdir(exist_ok=True, parents=True)

        # Remove the default stderr sink so loguru JSON doesn't interleave
        # with the daemon's stdlib logging output.
        # already removed by a prior EventLogger in this process
        with contextlib.suppress(ValueError):
            _loguru.remove(0)

        self._logger = _loguru.bind()
        self._events_id: int | None = None
        self._violations_id: int | None = None
        self._configure_sinks()

    def _configure_sinks(self) -> None:
        rotation = f"{self._config.max_size_mb} MB"
        retention = timedelta(days=self._config.retention_days)

        events_path = str(pathlib.Path(self._logs_dir) / "events.jsonl")
        violations_path = str(pathlib.Path(self._logs_dir) / "violations.jsonl")

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
        ts = datetime.now(tz=UTC).isoformat()
        common: dict[str, Any] = {
            "ts": ts,
            "rule": event.rule,
            "verdict": event.verdict,
            "confidence": (round(event.confidence, 4) if event.confidence is not None else None),
            "latency_ms": round(event.latency_ms, 1),
            "prompt_chars": event.prompt_chars,
            "tier": event.tier,
            "reason": event.reason or "",
            "input_snippet": (event.input_snippet or "")[:_MAX_SNIPPET_LOG_CHARS],
            "outcome": event.outcome,
            "action": event.action,
            "model": event.model,
            "downgrade": event.downgrade,
        }
        if event.kind is not None:
            common["kind"] = event.kind

        self._logger.bind(_sink="events").info(json.dumps(common, default=str))

        if event.action in _BLOCKING_ACTIONS and event.kind is None:
            violation = {**common}
            self._logger.bind(_sink="violations").info(json.dumps(violation, default=str))

    def close(self) -> None:
        """Remove sinks added by this logger."""
        if self._events_id is not None:
            self._logger.remove(self._events_id)
            self._events_id = None
        if self._violations_id is not None:
            self._logger.remove(self._violations_id)
            self._violations_id = None
