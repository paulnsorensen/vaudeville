"""Standalone pipeline effects: rewrite, escalate, run, and the origin label."""

from __future__ import annotations

from .escalate import escalate
from .origin_label import with_origin_label
from .rewrite import apply_rewrite, rewrite_or_feedback
from .run_command import run_named_command

__all__ = [
    "apply_rewrite",
    "escalate",
    "rewrite_or_feedback",
    "run_named_command",
    "with_origin_label",
]
