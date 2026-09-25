from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text


def styled_table(title: str, caption: str | None = None) -> Table:
    return Table(
        title=title,
        caption=caption,
        title_style="bold cyan",
        header_style="bold magenta",
        caption_style="dim",
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_lines=False,
        leading=0,
        padding=(0, 1),
    )


# Actions that gate the session; mirrors event_log._BLOCKING_ACTIONS (F23).
_VIOLATION_ACTIONS = frozenset({"block", "ask"})


def verdict_text(verdict: str, action: str | None = None) -> Text:
    """Style *verdict* for display.

    When *action* is given, redness follows the recorded action (F19):
    outcomes are rule-defined names, not always literally "violation".
    Without *action*, falls back to the literal "violation" string.
    """
    is_violation = (
        action in _VIOLATION_ACTIONS if action is not None else verdict == "violation"
    )
    if is_violation:
        return Text(verdict, style="bold red")
    return Text(verdict, style="bold green")


def tier_text(tier: str) -> Text:
    if tier == "disabled":
        return Text(tier, style="dim italic")
    if tier in ("shadow", "log"):
        return Text(tier, style="dim")
    if tier == "warn":
        return Text(tier, style="yellow")
    return Text(tier, style="bold red")


_CONFIDENCE_HIGH = 0.8
_CONFIDENCE_MEH = 0.5


def confidence_text(conf: float) -> Text:
    formatted = f"{conf:.2f}"
    if conf >= _CONFIDENCE_HIGH:
        return Text(formatted, style="green")
    if conf >= _CONFIDENCE_MEH:
        return Text(formatted, style="yellow")
    return Text(formatted, style="dim")


_LATENCY_OK = 100.0
_LATENCY_WARN = 500.0


def latency_text(ms: float) -> Text:
    formatted = f"{ms:.1f}"
    if ms <= _LATENCY_OK:
        return Text(formatted, style="green")
    if ms <= _LATENCY_WARN:
        return Text(formatted, style="yellow")
    return Text(formatted, style="red")
