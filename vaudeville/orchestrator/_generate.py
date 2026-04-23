"""Generate-loop orchestration: emit new rules, then tune any below threshold."""

from __future__ import annotations

import os
from pathlib import Path

from vaudeville.orchestrator import _abandon, _tune
from vaudeville.orchestrator._phase import (
    Thresholds,
    _build_threshold_args,
    _RalphRunner,
    _run_phase,
    _scoped_env,
    default_ralph_runner,
)


def _snapshot_rules(rules_dir: Path) -> set[str]:
    if not rules_dir.exists():
        return set()
    return {f.name for f in rules_dir.iterdir() if f.suffix in (".yaml", ".yml")}


def _tune_if_below_thresholds(
    name: str,
    thresholds: Thresholds,
    rounds: int,
    tuner_iters: int,
    project_root: str,
    commands_dir: str,
    runner: _RalphRunner,
    rules_dir: str,
) -> None:
    result = _abandon._eval_rule(name, project_root)
    needs_tune = result is None or (
        result.p_min < thresholds.p_min
        or result.r_min < thresholds.r_min
        or result.f1_min < thresholds.f1_min
    )
    if not needs_tune:
        return
    _tune.orchestrate_tune(
        name,
        thresholds,
        rounds,
        tuner_iters,
        project_root,
        commands_dir,
        runner,
        rules_dir=rules_dir,
    )


def orchestrate_generate(
    instructions: str,
    thresholds: Thresholds,
    rounds: int,
    tuner_iters: int,
    mode: str,
    project_root: str,
    commands_dir: str,
    runner: _RalphRunner = default_ralph_runner,
    *,
    rules_dir: str,
) -> int:
    """Run generate designer, then tune any rules that miss thresholds."""
    rules_dir_path = Path(rules_dir)
    before = _snapshot_rules(rules_dir_path)

    generate_dir = os.path.join(commands_dir, "generate")
    gen_args = [
        "-n",
        "1",
        "--instructions",
        instructions,
        *_build_threshold_args(thresholds),
        "--mode",
        mode,
        "--rules_dir",
        rules_dir,
    ]

    env = {"VAUDEVILLE_RULES_DIR": rules_dir, "VAUDEVILLE_PROJECT_CWD": project_root}
    with _scoped_env(env):
        _run_phase("generate", generate_dir, gen_args, project_root, runner)

        after = _snapshot_rules(rules_dir_path)
        new_rules = sorted(Path(f).stem for f in after - before)

        for name in new_rules:
            _tune_if_below_thresholds(
                name,
                thresholds,
                rounds,
                tuner_iters,
                project_root,
                commands_dir,
                runner,
                rules_dir,
            )

    return 0
