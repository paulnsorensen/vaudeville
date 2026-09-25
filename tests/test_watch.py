"""Tests for vaudeville.server.watch module."""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from vaudeville.server.watch import (
    _MAX_ROWS,
    _build_table,
    _parse_ts_display,
    _sanitize_display,
    watch,
)
from vaudeville.tui import (
    confidence_text as _confidence_text,
)
from vaudeville.tui import (
    tier_text as _tier_text,
)
from vaudeville.tui import (
    verdict_text as _verdict_text,
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


def test_tier_text_block() -> None:
    text = _tier_text("block")
    assert text.plain == "block"
    assert "red" in str(text.style)


def test_tier_text_log() -> None:
    text = _tier_text("log")
    assert text.plain == "log"
    assert "dim" in str(text.style)


def test_tier_text_disabled() -> None:
    text = _tier_text("disabled")
    assert text.plain == "disabled"
    style = str(text.style)
    assert "dim" in style and "italic" in style


# --- _confidence_text ---


def test_confidence_text_high() -> None:
    text = _confidence_text(0.95)
    assert text.plain == "0.95"
    assert "green" in str(text.style)


def test_confidence_text_high_at_threshold() -> None:
    text = _confidence_text(0.80)
    assert "green" in str(text.style)


def test_confidence_text_meh() -> None:
    text = _confidence_text(0.65)
    assert text.plain == "0.65"
    assert "yellow" in str(text.style)


def test_confidence_text_meh_at_threshold() -> None:
    text = _confidence_text(0.50)
    assert "yellow" in str(text.style)


def test_confidence_text_low() -> None:
    text = _confidence_text(0.20)
    assert text.plain == "0.20"
    assert "dim" in str(text.style)


# --- _build_table ---


def _make_event(
    rule: str = "test-rule",
    verdict: str = "clean",
    confidence: float = 0.95,
    latency_ms: float = 42.0,
    ts: str = "2024-01-15T10:30:45+00:00",
    action: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts": ts,
        "rule": rule,
        "verdict": verdict,
        "confidence": confidence,
        "latency_ms": latency_ms,
        "prompt_chars": 100,
    }
    if action is not None:
        event["action"] = action
    return event


def test_build_table_empty() -> None:
    table = _build_table([], (0, 0, 0))
    assert table.title == "Vaudeville \u2014 Live Rule Firings"
    assert "0 events" in (table.caption or "")
    assert table.row_count == 0


def test_build_table_with_events() -> None:
    events = [_make_event(), _make_event(verdict="violation", action="block")]
    table = _build_table(events, (2, 1, 0))
    assert table.row_count == 2
    assert "2 events" in (table.caption or "")
    assert "1 violations" in (table.caption or "")


def test_build_table_shows_dropped_count() -> None:
    table = _build_table([], (5, 1, 2))
    assert "2 dropped" in (table.caption or "")


def test_build_table_truncates_to_max_rows() -> None:
    events = [_make_event(rule=f"rule-{i}") for i in range(30)]
    table = _build_table(events, (30, 0, 0))
    assert table.row_count == _MAX_ROWS


def test_build_table_has_tier_column() -> None:
    events = [_make_event()]
    table = _build_table(events, (1, 0, 0))
    col_names = [c.header for c in table.columns]
    assert "Tier" in [str(h) for h in col_names]


def test_build_table_has_reason_and_llm_output_columns() -> None:
    events = [_make_event()]
    table = _build_table(events, (1, 0, 0))
    col_names = [str(c.header) for c in table.columns]
    assert "Reason" in col_names
    assert "LLM Output" in col_names


def test_build_table_has_action_and_downgrade_columns() -> None:
    events = [_make_event()]
    table = _build_table(events, (1, 0, 0))
    col_names = [str(c.header) for c in table.columns]
    assert "Action" in col_names
    assert "Downgrade" in col_names


def test_sanitize_display_short_text() -> None:
    assert _sanitize_display("short").plain == "short"


def test_sanitize_display_preserves_long_text() -> None:
    # No truncation: Rich wraps long text via column overflow="fold".
    assert _sanitize_display("x" * 200).plain == "x" * 200


def test_sanitize_display_sanitizes_newlines() -> None:
    assert _sanitize_display("line1\nline2\rline3").plain == "line1 line2 line3"


def test_sanitize_display_handles_none_and_empty() -> None:
    assert _sanitize_display(None).plain == ""
    assert _sanitize_display("").plain == ""
    assert _sanitize_display("   ").plain == ""


def test_build_table_missing_fields() -> None:
    events: list[dict[str, Any]] = [{}]
    table = _build_table(events, (1, 0, 0))
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

    assert pathlib.Path(log_path).exists()


def test_watch_reads_new_lines(tmp_path: Any) -> None:
    log_path = str(tmp_path / "events.jsonl")
    # Pre-create empty file
    with pathlib.Path(log_path).open("w"):
        pass

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Write an event after first poll
            with pathlib.Path(log_path).open("a") as f:
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
    with pathlib.Path(log_path).open("w"):
        pass

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with pathlib.Path(log_path).open("a") as f:
                f.write(json.dumps(_make_event(verdict="violation", action="block")) + "\n")
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
    with pathlib.Path(log_path).open("w"):
        pass

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with pathlib.Path(log_path).open("a") as f:
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
    with pathlib.Path(log_path).open("w") as f:
        f.write(json.dumps(_make_event(rule="old")) + "\n")

    call_count = 0

    def fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with pathlib.Path(log_path).open("a") as f:
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


# --- _read_new_events ---


def test_read_new_events_excludes_dropped_kind_from_display() -> None:
    """F18: a dropped row is not shown as a live row, but counts in the
    session totals under a separate dropped count."""
    from io import StringIO

    from vaudeville.server.watch import _read_new_events

    lines = (
        json.dumps({"rule": "a", "verdict": "violation", "action": "block", "kind": "dropped"})
        + "\n"
        + json.dumps({"rule": "b", "verdict": "clean"})
        + "\n"
    )
    events, (total_seen, violations, dropped), changed = _read_new_events(
        StringIO(lines), [], (0, 0, 0)
    )

    assert changed is True
    assert [e["rule"] for e in events] == ["b"]
    assert total_seen == 2
    assert violations == 0
    assert dropped == 1


def test_read_new_events_derives_violations_from_action() -> None:
    """F19: outcomes [unsafe, safe] mapped to action `block` count as a
    violation, regardless of the verdict string."""
    from io import StringIO

    from vaudeville.server.watch import _read_new_events

    lines = (
        json.dumps({"rule": "a", "verdict": "unsafe", "action": "block"})
        + "\n"
        + json.dumps({"rule": "b", "verdict": "safe", "action": "allow"})
        + "\n"
    )
    events, (total_seen, violations, dropped), changed = _read_new_events(
        StringIO(lines), [], (0, 0, 0)
    )

    assert changed is True
    assert total_seen == 2
    assert violations == 1
    assert dropped == 0
