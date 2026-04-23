"""Live TUI for watching classification events.

Tails ``events.jsonl`` and renders a continuously updating table of
the last 20 rule firings using Rich.
"""

from __future__ import annotations

import json
import os
import time
from typing import IO, Any

from rich.live import Live
from rich.table import Table
from rich.text import Text

from vaudeville.server.tui import (
    confidence_text as _confidence_text,
    latency_text as _latency_text,
    styled_table,
    tier_text as _tier_text,
    verdict_text as _verdict_text,
)

_EVENTS_LOG = os.path.join(
    os.path.expanduser("~"), ".vaudeville", "logs", "events.jsonl"
)

_MAX_ROWS = 20
_POLL_INTERVAL = 0.2

# Minimum column widths keep fixed-content columns readable on narrow terminals.
# Reason and Text flex to fill remaining space (and wrap) via `ratio`.
_TIME_MIN_WIDTH = 8
_RULE_MIN_WIDTH = 15
_TIER_MIN_WIDTH = 7
_VERDICT_MIN_WIDTH = 9
_CONFIDENCE_MIN_WIDTH = 10
_LATENCY_MIN_WIDTH = 10
_REASON_MIN_WIDTH = 20
_SNIPPET_MIN_WIDTH = 20


def _parse_ts_display(ts: str) -> str:
    # ISO format: 2024-01-15T10:30:45.123456+00:00
    try:
        time_part = ts.split("T")[1]
        return time_part[:8]
    except (IndexError, TypeError):
        return ts[:8] if ts else "??:??:??"


def _to_float(value: object) -> float:
    """Coerce *value* to float, returning 0.0 on failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _sanitize_display(value: object) -> Text:
    """Return *value* as single-line Text, with newlines flattened to spaces."""
    text = (
        ("" if value is None else str(value))
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )
    return Text(text)


def _build_table(events: list[dict[str, Any]], totals: tuple[int, int]) -> Table:
    total_seen, violations = totals
    table = styled_table(
        title="Vaudeville \u2014 Live Rule Firings",
        caption=f"Session: {total_seen} events, {violations} violations",
    )
    table.add_column("Time", style="dim", min_width=_TIME_MIN_WIDTH, no_wrap=True)
    table.add_column("Rule", min_width=_RULE_MIN_WIDTH, overflow="fold")
    table.add_column("Tier", min_width=_TIER_MIN_WIDTH, no_wrap=True)
    table.add_column("Verdict", min_width=_VERDICT_MIN_WIDTH, no_wrap=True)
    table.add_column(
        "Confidence",
        justify="right",
        min_width=_CONFIDENCE_MIN_WIDTH,
        no_wrap=True,
    )
    table.add_column(
        "Latency ms",
        justify="right",
        min_width=_LATENCY_MIN_WIDTH,
        no_wrap=True,
    )
    table.add_column("Reason", min_width=_REASON_MIN_WIDTH, ratio=1, overflow="fold")
    table.add_column(
        "LLM Output", min_width=_SNIPPET_MIN_WIDTH, ratio=1, overflow="fold"
    )

    for evt in events[-_MAX_ROWS:]:
        table.add_row(
            _parse_ts_display(evt.get("ts", "")),
            evt.get("rule", "<unknown>"),
            _tier_text(evt.get("tier", "enforce")),
            _verdict_text(evt.get("verdict", "?")),
            _confidence_text(_to_float(evt.get("confidence", 0))),
            _latency_text(_to_float(evt.get("latency_ms", 0))),
            _sanitize_display(evt.get("reason", "")),
            _sanitize_display(evt.get("input_snippet", "")),
        )
    return table


def _read_new_events(
    f: IO[str],
    events: list[dict[str, Any]],
    totals: tuple[int, int],
) -> tuple[list[dict[str, Any]], tuple[int, int], bool]:
    total_seen, violations = totals
    changed = False
    for line in f:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            evt = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        events.append(evt)
        total_seen += 1
        if evt.get("verdict") == "violation":
            violations += 1
        changed = True

    if len(events) > _MAX_ROWS:
        events = events[-_MAX_ROWS:]

    return events, (total_seen, violations), changed


def _ensure_log_exists(log_path: str) -> None:
    if not os.path.exists(log_path):
        parent = os.path.dirname(log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(log_path, "a"):
            pass


def watch(log_path: str = _EVENTS_LOG) -> None:
    _ensure_log_exists(log_path)

    events: list[dict[str, Any]] = []
    totals = (0, 0)

    with open(log_path) as f:
        f.seek(0, 2)  # seek to end

        with Live(_build_table(events, totals), refresh_per_second=5) as live:
            while True:
                events, totals, changed = _read_new_events(f, events, totals)
                if changed:
                    live.update(_build_table(events, totals))
                time.sleep(_POLL_INTERVAL)
