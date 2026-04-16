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

_EVENTS_LOG = os.path.join(
    os.path.expanduser("~"), ".vaudeville", "logs", "events.jsonl"
)

_MAX_ROWS = 20
_POLL_INTERVAL = 0.2
_REASON_DISPLAY_CHARS = 50
_SNIPPET_DISPLAY_CHARS = 50
# Rich adds horizontal padding around cell content; reserve a little extra width.
_REASON_COLUMN_WIDTH = _REASON_DISPLAY_CHARS + 2
_SNIPPET_COLUMN_WIDTH = _SNIPPET_DISPLAY_CHARS + 2


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


def _verdict_text(verdict: str) -> Text:
    if verdict == "violation":
        return Text(verdict, style="bold red")
    return Text(verdict, style="bold green")


def _tier_text(tier: str) -> Text:
    if tier == "shadow":
        return Text(tier, style="dim")
    if tier == "warn":
        return Text(tier, style="yellow")
    return Text(tier, style="bold green")


def _truncate_display(value: object, max_chars: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1] + "…"


def _build_table(events: list[dict[str, Any]], totals: tuple[int, int]) -> Table:
    total_seen, violations = totals
    table = Table(
        title="Vaudeville \u2014 Live Rule Firings",
        caption=f"Session: {total_seen} events, {violations} violations",
    )
    table.add_column("Time", style="dim", width=10)
    table.add_column("Rule", width=30)
    table.add_column("Tier", width=10)
    table.add_column("Verdict", width=12)
    table.add_column("Confidence", justify="right", width=12)
    table.add_column("Latency ms", justify="right", width=12)
    table.add_column("Reason", width=_REASON_COLUMN_WIDTH)
    table.add_column("Text", width=_SNIPPET_COLUMN_WIDTH)

    for evt in events[-_MAX_ROWS:]:
        table.add_row(
            _parse_ts_display(evt.get("ts", "")),
            evt.get("rule", "<unknown>"),
            _tier_text(evt.get("tier", "enforce")),
            _verdict_text(evt.get("verdict", "?")),
            f"{_to_float(evt.get('confidence', 0)):.2f}",
            f"{_to_float(evt.get('latency_ms', 0)):.1f}",
            _truncate_display(evt.get("reason", ""), _REASON_DISPLAY_CHARS),
            _truncate_display(evt.get("input_snippet", ""), _SNIPPET_DISPLAY_CHARS),
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
