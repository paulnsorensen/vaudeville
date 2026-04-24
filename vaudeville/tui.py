"""Shared Rich UI primitives for vaudeville terminal rendering."""

from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text


def styled_table(title: str, caption: str | None = None) -> Table:
    """Return a pre-configured Rich Table with the vaudeville house style."""
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


def verdict_text(verdict: str) -> Text:
    if verdict == "violation":
        return Text(verdict, style="bold red")
    return Text(verdict, style="bold green")


def tier_text(tier: str) -> Text:
    if tier == "shadow":
        return Text(tier, style="dim")
    if tier == "warn":
        return Text(tier, style="yellow")
    return Text(tier, style="bold green")


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
