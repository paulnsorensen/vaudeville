"""Multi-phase tune/generate orchestrator (public API).

Runs the designer → tuner → judge pipeline for up to K rounds.
Stdlib only — no native/platform deps.
"""

from __future__ import annotations

from vaudeville.orchestrator._abandon import (
    _eval_rule,
    _extract_abandon_reason,
    _locate_rule_file,
    abandon_rule,
)
from vaudeville.orchestrator._generate import orchestrate_generate
from vaudeville.orchestrator._phase import (
    EMPTY_PLAN,
    JudgeKind,
    JudgeParseError,
    JudgeVerdict,
    RalphError,
    Thresholds,
    _is_empty_plan,
    _RalphRunner,
    _run_phase,
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
    "_RalphRunner",
    "_eval_rule",
    "_extract_abandon_reason",
    "_is_empty_plan",
    "_locate_rule_file",
    "_run_phase",
    "abandon_rule",
    "default_ralph_runner",
    "orchestrate_generate",
    "orchestrate_tune",
    "parse_judge_signal",
]
