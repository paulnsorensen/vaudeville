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
import threading
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
_PROMISE_RE = re.compile(r"<promise>\s*THRESHOLDS_MET\s*</promise>")
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


def tuner_promised_done(output: str) -> bool:
    return bool(_PROMISE_RE.search(output))


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


def _run_streaming(
    cmd: list[str],
    project_root: str,
    on_line: Callable[[str], None],
) -> subprocess.CompletedProcess[str]:
    """Run cmd via Popen, fanning stdout lines through on_line in real time."""
    proc = subprocess.Popen(
        cmd,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _read_stdout() -> None:
        if proc.stdout is None:
            return
        for raw in proc.stdout:
            stripped = raw.rstrip("\n")
            stdout_lines.append(stripped)
            on_line(stripped)

    def _read_stderr() -> None:
        if proc.stderr is None:
            return
        for raw in proc.stderr:
            stderr_lines.append(raw.rstrip("\n"))

    t_out = threading.Thread(target=_read_stdout)
    t_err = threading.Thread(target=_read_stderr)
    t_out.start()
    t_err.start()
    t_out.join()
    t_err.join()
    proc.wait()

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
    )


def default_ralph_runner(
    ralph_dir: str,
    extra_args: list[str],
    project_root: str,
    *,
    on_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ralph, streaming output line-by-line via on_line when provided."""
    cmd = ["ralph", "run", ralph_dir, *extra_args]
    try:
        if on_line is None:
            return subprocess.run(
                cmd, cwd=project_root, capture_output=True, text=True, check=False
            )
        return _run_streaming(cmd, project_root, on_line)
    except FileNotFoundError as e:
        raise RalphError(f"ralph not found: {e}") from e


def _make_runner(
    runner: _RalphRunner,
    on_line: Callable[[str], None] | None = None,
) -> _RalphRunner:
    """Wrap runner to call on_line per stdout line (post-hoc for non-default runners)."""
    if on_line is None:
        return runner
    if runner is default_ralph_runner:

        def _streaming(
            ralph_dir: str, extra_args: list[str], project_root: str
        ) -> subprocess.CompletedProcess[str]:
            return default_ralph_runner(
                ralph_dir, extra_args, project_root, on_line=on_line
            )

        return _streaming

    def _posthoc(
        ralph_dir: str, extra_args: list[str], project_root: str
    ) -> subprocess.CompletedProcess[str]:
        result = runner(ralph_dir, extra_args, project_root)
        for line in (result.stdout or "").splitlines():
            on_line(line)
        return result

    return _posthoc


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
