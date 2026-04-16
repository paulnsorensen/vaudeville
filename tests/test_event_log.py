"""Tests for vaudeville.server.event_log."""

from __future__ import annotations

import json
import pathlib
import time

from vaudeville.server.event_log import ClassificationEvent, EventLogger
from vaudeville.server.log_config import LogConfig


def _read_jsonl(path: pathlib.Path) -> list[dict[str, object]]:
    """Parse all lines from a JSONL file."""
    lines = path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


def test_log_event_writes_events_jsonl(tmp_path: pathlib.Path) -> None:
    """All classifications appear in events.jsonl."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="no-hedging",
                verdict="clean",
                confidence=0.92,
                latency_ms=42.3,
                prompt_chars=150,
            )
        )
        # Give loguru a moment to flush
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert len(events) == 1
        evt = events[0]
        assert evt["rule"] == "no-hedging"
        assert evt["verdict"] == "clean"
        assert evt["confidence"] == 0.92
        assert evt["latency_ms"] == 42.3
        assert evt["prompt_chars"] == 150
        assert evt["reason"] == ""
        assert evt["input_snippet"] == ""
        assert "ts" in evt
    finally:
        logger.close()


def test_clean_verdict_not_in_violations(tmp_path: pathlib.Path) -> None:
    """Clean verdicts do not appear in violations.jsonl."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="no-hedging",
                verdict="clean",
                confidence=0.92,
                latency_ms=42.3,
                prompt_chars=150,
            )
        )
        time.sleep(0.05)

        violations_path = tmp_path / "violations.jsonl"
        assert not violations_path.exists() or violations_path.read_text().strip() == ""
    finally:
        logger.close()


def test_violation_written_to_both_files(tmp_path: pathlib.Path) -> None:
    """Violations appear in both events.jsonl and violations.jsonl."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="no-sycophancy",
                verdict="violation",
                confidence=0.88,
                latency_ms=55.1,
                prompt_chars=200,
                reason="Unearned praise detected",
                input_snippet="Great question! That's a really smart approach.",
            )
        )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert len(events) == 1
        assert events[0]["verdict"] == "violation"
        assert events[0]["reason"] == "Unearned praise detected"
        assert (
            events[0]["input_snippet"]
            == "Great question! That's a really smart approach."
        )

        violations = _read_jsonl(tmp_path / "violations.jsonl")
        assert len(violations) == 1
        v = violations[0]
        assert v["rule"] == "no-sycophancy"
        assert v["verdict"] == "violation"
        assert v["reason"] == "Unearned praise detected"
        assert v["input_snippet"] == "Great question! That's a really smart approach."
    finally:
        logger.close()


def test_input_snippet_truncated_at_500(tmp_path: pathlib.Path) -> None:
    """Input snippet is truncated to 500 characters."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        long_snippet = "x" * 1000
        logger.log_event(
            ClassificationEvent(
                rule="test-rule",
                verdict="violation",
                confidence=0.75,
                latency_ms=30.0,
                prompt_chars=1000,
                reason="too long",
                input_snippet=long_snippet,
            )
        )
        time.sleep(0.05)

        violations = _read_jsonl(tmp_path / "violations.jsonl")
        assert len(violations[0]["input_snippet"]) == 500  # type: ignore[arg-type]
    finally:
        logger.close()


def test_event_input_snippet_truncated_at_500(tmp_path: pathlib.Path) -> None:
    """events.jsonl also truncates input snippet to 500 characters."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        long_snippet = "y" * 1000
        logger.log_event(
            ClassificationEvent(
                rule="test-rule",
                verdict="clean",
                confidence=0.75,
                latency_ms=30.0,
                prompt_chars=1000,
                reason="long",
                input_snippet=long_snippet,
            )
        )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert len(events[0]["input_snippet"]) == 500  # type: ignore[arg-type]
    finally:
        logger.close()


def test_multiple_events(tmp_path: pathlib.Path) -> None:
    """Multiple events are appended correctly."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        for i in range(3):
            logger.log_event(
                ClassificationEvent(
                    rule=f"rule-{i}",
                    verdict="clean",
                    confidence=0.9,
                    latency_ms=10.0,
                    prompt_chars=50,
                )
            )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert len(events) == 3
        assert [e["rule"] for e in events] == ["rule-0", "rule-1", "rule-2"]
    finally:
        logger.close()


def test_creates_logs_directory(tmp_path: pathlib.Path) -> None:
    """EventLogger creates the logs directory if absent."""
    logs_dir = tmp_path / "nested" / "logs"
    logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
    try:
        assert logs_dir.is_dir()
    finally:
        logger.close()


def test_default_config_loaded(tmp_path: pathlib.Path) -> None:
    """When no config is passed, defaults are loaded."""
    logger = EventLogger(logs_dir=str(tmp_path))
    try:
        assert logger._config.retention_days == 7
        assert logger._config.max_size_mb == 10
    finally:
        logger.close()


def test_close_removes_sinks(tmp_path: pathlib.Path) -> None:
    """After close(), sink IDs are cleared."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    assert logger._events_id is not None
    assert logger._violations_id is not None
    logger.close()
    assert logger._events_id is None
    assert logger._violations_id is None


def test_close_idempotent(tmp_path: pathlib.Path) -> None:
    """Calling close() twice does not raise."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    logger.close()
    logger.close()  # should not raise


def test_confidence_rounded(tmp_path: pathlib.Path) -> None:
    """Confidence is rounded to 4 decimal places."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="test",
                verdict="clean",
                confidence=0.123456789,
                latency_ms=10.0,
                prompt_chars=50,
            )
        )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert events[0]["confidence"] == 0.1235
    finally:
        logger.close()


def test_tier_included_in_event(tmp_path: pathlib.Path) -> None:
    """Tier field is written to events.jsonl."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="test-shadow",
                verdict="violation",
                confidence=0.85,
                latency_ms=20.0,
                prompt_chars=100,
                reason="hedging",
                tier="shadow",
            )
        )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert events[0]["tier"] == "shadow"
    finally:
        logger.close()


def test_tier_defaults_to_enforce(tmp_path: pathlib.Path) -> None:
    """Tier defaults to enforce when not specified."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="test",
                verdict="clean",
                confidence=0.9,
                latency_ms=10.0,
                prompt_chars=50,
            )
        )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert events[0]["tier"] == "enforce"
    finally:
        logger.close()


def test_latency_rounded(tmp_path: pathlib.Path) -> None:
    """Latency is rounded to 1 decimal place."""
    logger = EventLogger(config=LogConfig(), logs_dir=str(tmp_path))
    try:
        logger.log_event(
            ClassificationEvent(
                rule="test",
                verdict="clean",
                confidence=0.9,
                latency_ms=42.3456,
                prompt_chars=50,
            )
        )
        time.sleep(0.05)

        events = _read_jsonl(tmp_path / "events.jsonl")
        assert events[0]["latency_ms"] == 42.3
    finally:
        logger.close()
