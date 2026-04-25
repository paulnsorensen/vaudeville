"""Multi-phase tune/generate orchestrator (public API).

Runs the designer → tuner → judge pipeline for up to K rounds.
Stdlib only — no native/platform deps.
"""

from __future__ import annotations

from vaudeville.orchestrator._abandon import abandon_rule
from vaudeville.orchestrator._default_prompt import build_default_instructions
from vaudeville.orchestrator._generate import orchestrate_generate
from vaudeville.orchestrator._phase import (
    EMPTY_PLAN,
    JudgeKind,
    JudgeParseError,
    JudgeVerdict,
    RalphError,
    Thresholds,
    default_ralph_runner,
    parse_judge_signal,
)
from vaudeville.orchestrator._tune import orchestrate_tune

__all__ = [
    "EMPTY_PLAN",
    "JudgeKind",
    "JudgeParseError",
    "JudgeVerdict",
    "RalphError",
    "Thresholds",
    "abandon_rule",
    "build_default_instructions",
    "default_ralph_runner",
    "orchestrate_generate",
    "orchestrate_tune",
    "parse_judge_signal",
]
