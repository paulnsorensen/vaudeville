"""Tests for vaudeville.server.watch module."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from vaudeville.server.watch import (
    _MAX_ROWS,
    _build_table,
    _parse_ts_display,
    _tier_text,
    _truncate_display,
    _verdict_text,
    watch,
)


# --- _parse_ts_display ---


def test_parse_ts_display_iso() -> None:
    assert _parse_ts_display("2024-01-15T10:30:45.123456+00:00") == "10:30:45"


def test_parse_ts_display_no_t() -> None:
    result = _parse_ts_display("not-an-iso")
    assert result == "not-an-i"


def test_parse_ts_display_empty() -> None:
    assert _parse_ts_display("") == "??:??:??"


def test_parse_ts_display_short_time() -> None:
    assert _parse_ts_display("2024-01-15T10:30") == "10:30"


# --- _verdict_text ---


def test_verdict_text_violation() -> None:
    text = _verdict_text("violation")
    assert text.plain == "violation"
    assert "red" in str(text.style)


def test_verdict_text_clean() -> None:
    text = _verdict_text("clean")
    assert text.plain == "clean"
    assert "green" in str(text.style)


# --- _tier_text ---


def test_tier_text_shadow() -> None:
    text = _tier_text("shadow")
    assert text.plain == "shadow"
    assert "dim" in str(text.style)


def test_tier_text_warn() -> None:
    text = _tier_text("warn")
    assert text.plain == "warn"
    assert "yellow" in str(text.style)


def test_tier_text_enforce() -> None:
    text = _tier_text("enforce")
    assert text.plain == "enforce"
    assert "green" in str(text.style)


# --- _build_table ---


def _make_event(
    rule: str = "test-rule",
    verdict: str = "clean",
    confidence: float = 0.95,
    latency_ms: float = 42.0,
    ts: str = "2024-01-15T10:30:45+00:00",
) -> dict[str, Any]:
    return {
        "ts": ts,
        "rule": rule,
        "verdict": verdict,
        "confidence": confidence,
        "latency_ms": latency_ms,
        "prompt_chars": 100,
    }


def test_build_table_empty() -> None:
    table = _build_table([], (0, 0))
    assert table.title == "Vaudeville \u2014 Live Rule Firings"
    assert "0 events" in (table.caption or "")
    assert table.row_count == 0


def test_build_table_with_events() -> None:
    events = [_make_event(), _make_event(verdict="violation")]
    table = _build_table(events, (2, 1))
    assert table.row_count == 2
    assert "2 events" in (table.caption or "")
    assert "1 violations" in (table.caption or "")


def test_build_table_truncates_to_max_rows() -> None:
    events = [_make_event(rule=f"rule-{i}") for i in range(30)]
    table = _build_table(events, (30, 0))
    assert table.row_count == _MAX_ROWS


def test_build_table_has_tier_column() -> None:
    events = [_make_event()]
    table = _build_table(events, (1, 0))
    col_names = [c.header for c in table.columns]
    assert "Tier" in [str(h) for h in col_names]


def test_build_table_has_reason_and_text_columns() -> None:
    events = [_make_event()]
    table = _build_table(events, (1, 0))
    col_names = [str(c.header) for c in table.columns]
    assert "Reason" in col_names
    assert "Text" in col_names


def test_truncate_display_short_text() -> None:
    assert _truncate_display("short", 10) == "short"


def test_truncate_display_long_text() -> None:
    assert _truncate_display("x" * 20, 10) == ("x" * 9) + "…"


def test_truncate_display_sanitizes_newlines() -> None:
    assert _truncate_display("line1\nline2\rline3", 100) == "line1 line2 line3"


def test_truncate_display_handles_tiny_widths() -> None:
    assert _truncate_display("abc", 1) == "…"
    assert _truncate_display("abc", 0) == ""


def test_build_table_missing_fields() -> None:
    events: list[dict[str, Any]] = [{}]
    table = _build_table(events, (1, 0))
    assert table.row_count == 1


# --- watch() ---


def test_watch_creates_log_file(tmp_path: Any) -> None:
    log_path = str(tmp_path / "subdir" / "events.jsonl")

    with patch("vaudeville.server.watch.Live") as mock_live:
        ctx = MagicMock()
        mock_live.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_live.return_value.__exit__ = MagicMock(return_value=False)

        with patch("vaudeville.server.watch.time") as mock_time:
            mock_time.sleep.side_effect = KeyboardInterrupt

            with pytest.raises(KeyboardInterrupt):
                watch(log_path=log_path)

    assert os.path.exists(log_path)


def test_watch_reads_new_lines(tmp_path: Any) -> None:
    log_path = str(tmp_path / "events.jsonl")
    # Pre-create empty file
    with open(log_path, "w"):
        pass

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Write an event after first poll
            with open(log_path, "a") as f:
                f.write(json.dumps(_make_event()) + "\n")
        elif call_count >= 3:
            raise KeyboardInterrupt

    with patch("vaudeville.server.watch.Live") as mock_live:
        ctx = MagicMock()
        mock_live.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_live.return_value.__exit__ = MagicMock(return_value=False)

        with patch("vaudeville.server.watch.time") as mock_time:
            mock_time.sleep = fake_sleep

            with pytest.raises(KeyboardInterrupt):
                watch(log_path=log_path)

        # Table should have been updated at least once
        assert ctx.update.call_count >= 1


def test_watch_counts_violations(tmp_path: Any) -> None:
    log_path = str(tmp_path / "events.jsonl")
    with open(log_path, "w"):
        pass

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with open(log_path, "a") as f:
                f.write(json.dumps(_make_event(verdict="violation")) + "\n")
                f.write(json.dumps(_make_event(verdict="clean")) + "\n")
        elif call_count >= 3:
            raise KeyboardInterrupt

    with patch("vaudeville.server.watch.Live") as mock_live:
        ctx = MagicMock()
        mock_live.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_live.return_value.__exit__ = MagicMock(return_value=False)

        with patch("vaudeville.server.watch.time") as mock_time:
            mock_time.sleep = fake_sleep

            with pytest.raises(KeyboardInterrupt):
                watch(log_path=log_path)

        # Verify the table was built with correct counts
        last_call = ctx.update.call_args
        table = last_call[0][0]
        assert "1 violations" in (table.caption or "")


def test_watch_skips_malformed_lines(tmp_path: Any) -> None:
    log_path = str(tmp_path / "events.jsonl")
    with open(log_path, "w"):
        pass

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with open(log_path, "a") as f:
                f.write("not valid json\n")
                f.write("\n")
                f.write(json.dumps(_make_event()) + "\n")
        elif call_count >= 3:
            raise KeyboardInterrupt

    with patch("vaudeville.server.watch.Live") as mock_live:
        ctx = MagicMock()
        mock_live.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_live.return_value.__exit__ = MagicMock(return_value=False)

        with patch("vaudeville.server.watch.time") as mock_time:
            mock_time.sleep = fake_sleep

            with pytest.raises(KeyboardInterrupt):
                watch(log_path=log_path)

        # Only the valid event should appear
        last_call = ctx.update.call_args
        table = last_call[0][0]
        assert "1 events" in (table.caption or "")


def test_watch_seeks_to_end(tmp_path: Any) -> None:
    """Pre-existing lines are skipped; only new lines are shown."""
    log_path = str(tmp_path / "events.jsonl")
    with open(log_path, "w") as f:
        f.write(json.dumps(_make_event(rule="old")) + "\n")

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with open(log_path, "a") as f:
                f.write(json.dumps(_make_event(rule="new")) + "\n")
        elif call_count >= 3:
            raise KeyboardInterrupt

    with patch("vaudeville.server.watch.Live") as mock_live:
        ctx = MagicMock()
        mock_live.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_live.return_value.__exit__ = MagicMock(return_value=False)

        with patch("vaudeville.server.watch.time") as mock_time:
            mock_time.sleep = fake_sleep

            with pytest.raises(KeyboardInterrupt):
                watch(log_path=log_path)

        last_call = ctx.update.call_args
        table = last_call[0][0]
        # Should only see 1 event (the new one), not the pre-existing one
        assert "1 events" in (table.caption or "")
