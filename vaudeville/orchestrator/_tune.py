"""Tune-loop orchestration: design → tune → judge for one rule."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from vaudeville.orchestrator import _abandon
from vaudeville.orchestrator._phase import (
    JudgeVerdict,
    Thresholds,
    _build_phase_args,
    _is_empty_plan,
    _RalphRunner,
    _run_phase,
    _scoped_env,
    default_ralph_runner,
    parse_judge_signal,
)


@dataclass(frozen=True)
class _TuneCtx:
    """Bundled tune-loop state passed to round helpers."""

    rule_name: str
    rules_dir: str
    project_root: str
    design_dir: str
    tune_dir: str
    judge_dir: str
    tuner_iters: int
    runner: _RalphRunner


def _run_tune_round(
    ctx: _TuneCtx,
    thresholds: Thresholds,
    plan_file: Path,
    skip_design: bool,
) -> tuple[JudgeVerdict, str]:
    """Run a single design→tune→judge round; return verdict and judge stdout."""
    phase_args = _build_phase_args(ctx.rule_name, thresholds, ctx.rules_dir)
    proj, runner = ctx.project_root, ctx.runner

    if not skip_design:
        _run_phase("design", ctx.design_dir, ["-n", "1"] + phase_args, proj, runner)

    if not _is_empty_plan(plan_file):
        _run_phase(
            "tune",
            ctx.tune_dir,
            ["-n", str(ctx.tuner_iters)] + phase_args,
            proj,
            runner,
        )

    result: subprocess.CompletedProcess[str] = _run_phase(
        "judge", ctx.judge_dir, ["-n", "1"] + phase_args, proj, runner
    )
    return parse_judge_signal(result.stdout), result.stdout


def orchestrate_tune(
    rule_name: str,
    thresholds: Thresholds,
    rounds: int,
    tuner_iters: int,
    project_root: str,
    commands_dir: str,
    runner: _RalphRunner = default_ralph_runner,
    *,
    rules_dir: str,
) -> int:
    """Run designer → tuner → judge for up to `rounds` iterations."""
    ctx = _TuneCtx(
        rule_name=rule_name,
        rules_dir=rules_dir,
        project_root=project_root,
        design_dir=os.path.join(commands_dir, "design"),
        tune_dir=os.path.join(commands_dir, "tune"),
        judge_dir=os.path.join(commands_dir, "judge"),
        tuner_iters=tuner_iters,
        runner=runner,
    )
    plan_file = (
        Path(project_root) / "commands" / "tune" / "state" / f"{rule_name}.plan.md"
    )

    verdict = JudgeVerdict(kind="JUDGE_CONTINUE_RE_DESIGN")
    judge_stdout = ""

    with _scoped_env({"VAUDEVILLE_RULES_DIR": rules_dir}):
        for _round in range(rounds):
            skip_design = verdict.kind not in (
                "JUDGE_CONTINUE_RE_DESIGN",
                "JUDGE_RAISE",
            )
            verdict, judge_stdout = _run_tune_round(
                ctx, thresholds, plan_file, skip_design
            )
            if verdict.kind in ("JUDGE_DONE", "JUDGE_ABANDON"):
                break
            if verdict.kind == "JUDGE_RAISE" and verdict.raised is not None:
                thresholds = verdict.raised

    if verdict.kind == "JUDGE_ABANDON":
        _abandon.abandon_with_metrics(
            rule_name, judge_stdout, verdict.raw_line, project_root, rules_dir
        )

    return 0
