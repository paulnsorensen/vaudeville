"""Tune-loop orchestration: design → tune → judge for one rule."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from vaudeville.orchestrator import _abandon
from vaudeville.orchestrator._phase import (
    JudgeVerdict,
    Thresholds,
    _build_phase_args,
    _is_empty_plan,
    _make_runner,
    _RalphRunner,
    _run_phase,
    _scoped_env,
    default_ralph_runner,
    parse_judge_signal,
    tuner_promised_done,
)

if TYPE_CHECKING:
    from vaudeville.orchestrator_tui import OrchestratorTUI

_log = logging.getLogger("vaudeville.orchestrator")


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
    tui: OrchestratorTUI | None = None,
) -> tuple[JudgeVerdict, str, bool]:
    phase_args = _build_phase_args(ctx.rule_name, thresholds, ctx.rules_dir)
    proj, runner = ctx.project_root, ctx.runner

    if not skip_design:
        _abandon.capture_eval_log(ctx.rule_name, proj)
        if tui:
            tui.update_phase("design", ctx.rule_name)
        _run_phase("design", ctx.design_dir, ["-n", "1"] + phase_args, proj, runner)

    tuner_promised = False
    if not _is_empty_plan(plan_file):
        if tui:
            tui.update_phase("tune", ctx.rule_name)
        tune_result = _run_phase(
            "tune",
            ctx.tune_dir,
            ["-n", str(ctx.tuner_iters)] + phase_args,
            proj,
            runner,
        )
        tuner_promised = tuner_promised_done(tune_result.stdout)

    if tui:
        tui.update_phase("judge", ctx.rule_name)
    result: subprocess.CompletedProcess[str] = _run_phase(
        "judge", ctx.judge_dir, ["-n", "1"] + phase_args, proj, runner
    )
    return parse_judge_signal(result.stdout), result.stdout, tuner_promised


def _should_exit(
    verdict: JudgeVerdict,
    tuner_promised: bool,
    plan_file: Path,
    round_idx: int,
) -> bool:
    """Return True if the tune loop should stop after this round."""
    if verdict.kind in ("JUDGE_DONE", "JUDGE_ABANDON"):
        _log.info("exit: %s after round %d", verdict.kind, round_idx + 1)
        return True
    if tuner_promised and verdict.kind != "JUDGE_RAISE":
        _log.info(
            "exit: tuner promised THRESHOLDS_MET (verdict=%s, round=%d)",
            verdict.kind,
            round_idx + 1,
        )
        return True
    if _is_empty_plan(plan_file) and verdict.kind.startswith("JUDGE_CONTINUE_"):
        _log.info(
            "exit: EMPTY_PLAN runaway guard (verdict=%s, round=%d)",
            verdict.kind,
            round_idx + 1,
        )
        return True
    return False


def _execute_round(
    ctx: _TuneCtx,
    thresholds: Thresholds,
    plan_file: Path,
    prev_verdict: JudgeVerdict,
    round_idx: int,
    rounds: int,
    tui: OrchestratorTUI | None,
) -> tuple[JudgeVerdict, str, bool]:
    """Drive one design→tune→judge cycle, surfacing the round's verdict."""
    if tui:
        tui.update_phase("orchestrating", ctx.rule_name, round_idx + 1, rounds)
    skip_design = prev_verdict.kind not in ("JUDGE_CONTINUE_RE_DESIGN", "JUDGE_RAISE")
    verdict, judge_stdout, tuner_promised = _run_tune_round(
        ctx, thresholds, plan_file, skip_design, tui=tui
    )
    if tui:
        tui.update_verdict(verdict.kind)
    return verdict, judge_stdout, tuner_promised


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
    tui: OrchestratorTUI | None = None,
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
        runner=_make_runner(runner, tui.append_line if tui else None),
    )
    plan_file = (
        Path(project_root) / "commands" / "tune" / "state" / f"{rule_name}.plan.md"
    )
    verdict = JudgeVerdict(kind="JUDGE_CONTINUE_RE_DESIGN")
    judge_stdout = ""

    with _scoped_env({"VAUDEVILLE_RULES_DIR": rules_dir}):
        for round_idx in range(rounds):
            verdict, judge_stdout, tuner_promised = _execute_round(
                ctx, thresholds, plan_file, verdict, round_idx, rounds, tui
            )
            if _should_exit(verdict, tuner_promised, plan_file, round_idx):
                break
            if verdict.kind == "JUDGE_RAISE" and verdict.raised is not None:
                thresholds = verdict.raised

    if verdict.kind == "JUDGE_ABANDON":
        _abandon.abandon_with_metrics(
            rule_name, judge_stdout, verdict.raw_line, project_root, rules_dir
        )

    return 0
