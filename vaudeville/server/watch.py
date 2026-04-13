"""Live TUI for watching classification events.

Tails ``events.jsonl`` and renders a continuously updating table of
the last 20 rule firings using Rich.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from rich.live import Live
from rich.table import Table
from rich.text import Text

_EVENTS_LOG = os.path.join(
    os.path.expanduser("~"), ".vaudeville", "logs", "events.jsonl"
)

_MAX_ROWS = 20
_POLL_INTERVAL = 0.2


def _parse_ts_display(ts: str) -> str:
    """Extract HH:MM:SS from an ISO timestamp."""
    # ISO format: 2024-01-15T10:30:45.123456+00:00
    try:
        time_part = ts.split("T")[1]
        return time_part[:8]
    except (IndexError, TypeError):
        return ts[:8] if ts else "??:??:??"


def _verdict_text(verdict: str) -> Text:
    """Colour-code a verdict string."""
    if verdict == "violation":
        return Text(verdict, style="bold red")
    return Text(verdict, style="bold green")


def _build_table(events: list[dict[str, Any]], totals: tuple[int, int]) -> Table:
    """Build a Rich table from the last *_MAX_ROWS* events."""
    total_seen, violations = totals
    table = Table(
        title="Vaudeville \u2014 Live Rule Firings",
        caption=f"Session: {total_seen} events, {violations} violations",
    )
    table.add_column("Time", style="dim", width=10)
    table.add_column("Rule", width=30)
    table.add_column("Verdict", width=12)
    table.add_column("Confidence", justify="right", width=12)
    table.add_column("Latency ms", justify="right", width=12)

    for evt in events[-_MAX_ROWS:]:
        table.add_row(
            _parse_ts_display(evt.get("ts", "")),
            evt.get("rule", "<unknown>"),
            _verdict_text(evt.get("verdict", "?")),
            f"{evt.get('confidence', 0):.2f}",
            f"{evt.get('latency_ms', 0):.1f}",
        )
    return table


def watch(log_path: str = _EVENTS_LOG) -> None:
    """Tail *log_path* and render a live table until interrupted."""
    if not os.path.exists(log_path):
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        # Touch the file so we can open it
        with open(log_path, "a"):
            pass

    events: list[dict[str, Any]] = []
    total_seen = 0
    violations = 0

    with open(log_path) as f:
        f.seek(0, 2)  # seek to end

        with Live(
            _build_table(events, (total_seen, violations)), refresh_per_second=5
        ) as live:
            while True:
                new_lines = f.readlines()
                for line in new_lines:
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

                # Keep only last _MAX_ROWS in memory
                if len(events) > _MAX_ROWS:
                    events = events[-_MAX_ROWS:]

                if new_lines:
                    live.update(_build_table(events, (total_seen, violations)))

                time.sleep(_POLL_INTERVAL)
