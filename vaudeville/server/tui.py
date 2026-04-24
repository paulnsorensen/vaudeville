"""Compatibility re-export for legacy imports from vaudeville.server.tui."""

from ..tui import (
    confidence_text,
    latency_text,
    styled_table,
    tier_text,
    verdict_text,
)

__all__ = [
    "confidence_text",
    "latency_text",
    "styled_table",
    "tier_text",
    "verdict_text",
]
