"""Phase execution primitives for the orchestrator.

Wraps the ralph subprocess invocation, threshold/argument formatting,
plan-file inspection, and scoped env-var management.
Stdlib only — no native/platform deps.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, cast


@dataclass(frozen=True)
class Thresholds:
    p_min: float
    r_min: float
    f1_min: float


JudgeKind = Literal[
    "JUDGE_DONE",
    "JUDGE_ABANDON",
    "JUDGE_RAISE",
    "JUDGE_CONTINUE_RE_DESIGN",
    "JUDGE_CONTINUE_TUNE_MORE",
    "JUDGE_CONTINUE_KEEP_STATE",
]


@dataclass(frozen=True)
class JudgeVerdict:
    kind: JudgeKind
    raised: Thresholds | None = None
    raw_line: str = ""


class RalphError(RuntimeError):
    pass


class JudgeParseError(RuntimeError):
    pass


_RalphRunner = Callable[[str, list[str], str], "subprocess.CompletedProcess[str]"]

_RAISE_RE = re.compile(r"^JUDGE_RAISE\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$")
EMPTY_PLAN = "EMPTY_PLAN"
_VALID_KINDS: frozenset[JudgeKind] = frozenset(
    (
        "JUDGE_DONE",
        "JUDGE_ABANDON",
        "JUDGE_RAISE",
        "JUDGE_CONTINUE_RE_DESIGN",
        "JUDGE_CONTINUE_TUNE_MORE",
        "JUDGE_CONTINUE_KEEP_STATE",
    )
)


def parse_judge_signal(output: str) -> JudgeVerdict:
    """Extract the final JUDGE_* signal from ralph stdout, scanning bottom-up."""
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("JUDGE_"):
            continue
        if stripped.startswith("JUDGE_RAISE"):
            m = _RAISE_RE.match(stripped)
            if not m:
                raise JudgeParseError(f"malformed JUDGE_RAISE: {stripped!r}")
            try:
                p, r, f1 = float(m.group(1)), float(m.group(2)), float(m.group(3))
            except ValueError:
                raise JudgeParseError(f"malformed JUDGE_RAISE floats: {stripped!r}")
            if not all(0.0 <= v <= 1.0 for v in (p, r, f1)):
                raise JudgeParseError(f"thresholds out of [0,1]: {stripped!r}")
            return JudgeVerdict(
                kind="JUDGE_RAISE", raised=Thresholds(p, r, f1), raw_line=stripped
            )
        if stripped not in _VALID_KINDS:
            raise JudgeParseError(f"unknown JUDGE_* signal: {stripped!r}")
        return JudgeVerdict(kind=cast(JudgeKind, stripped), raw_line=stripped)
    raise JudgeParseError("no JUDGE_* signal found in output")


def default_ralph_runner(
    ralph_dir: str, extra_args: list[str], project_root: str
) -> subprocess.CompletedProcess[str]:
    """Run ralph with captured stdout/stderr. Caller prints output as needed."""
    cmd = ["ralph", "run", ralph_dir, *extra_args]
    try:
        return subprocess.run(
            cmd, cwd=project_root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise RalphError(f"ralph not found: {e}") from e


def _build_threshold_args(thresholds: Thresholds) -> list[str]:
    return [
        "--p_min",
        str(thresholds.p_min),
        "--r_min",
        str(thresholds.r_min),
        "--f1_min",
        str(thresholds.f1_min),
    ]


def _build_phase_args(
    rule_name: str, thresholds: Thresholds, rules_dir: str
) -> list[str]:
    return [
        "--rule_name",
        rule_name,
        *_build_threshold_args(thresholds),
        "--rules_dir",
        rules_dir,
    ]


def _run_phase(
    phase_name: str,
    ralph_dir: str,
    extra_args: list[str],
    project_root: str,
    runner: _RalphRunner,
) -> subprocess.CompletedProcess[str]:
    result = runner(ralph_dir, extra_args, project_root)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RalphError(
            f"{phase_name} phase failed: ralph exit {result.returncode}"
            + (f"\n{tail}" if tail else "")
        )
    return result


def _is_empty_plan(plan_file: Path) -> bool:
    """Return True if the plan file contains the EMPTY_PLAN sentinel line."""
    if not plan_file.exists():
        return False
    return any(
        line.strip() == EMPTY_PLAN for line in plan_file.read_text().splitlines()
    )


@contextlib.contextmanager
def _scoped_env(updates: dict[str, str]) -> Iterator[None]:
    """Temporarily set env vars, restoring (or unsetting) prior values on exit."""
    prior: dict[str, str | None] = {k: os.environ.get(k) for k in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, prev in prior.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
