"""Multi-phase tune/generate orchestrator.

Runs the designer → tuner → judge pipeline for up to K rounds.
Stdlib only — no native/platform deps.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
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


def _locate_rule_file(rule_name: str, project_root: str) -> Path:
    """Find the rule YAML by searching project path then user home."""
    home = os.path.expanduser("~")
    home_rules = Path(home) / ".vaudeville" / "rules"
    candidates = [
        Path(project_root) / ".vaudeville" / "rules" / f"{rule_name}.yaml",
        Path(project_root) / ".vaudeville" / "rules" / f"{rule_name}.yml",
        home_rules / f"{rule_name}.yaml",
        home_rules / f"{rule_name}.yml",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"rule file not found for {rule_name!r}")


def abandon_rule(
    rule_name: str, reason: str, metrics: dict[str, object], project_root: str
) -> None:
    """Disable rule tier, append ABANDONED comment, and log to abandoned.jsonl."""
    rule_file = _locate_rule_file(rule_name, project_root)
    content = rule_file.read_text()

    sanitized = reason.replace("\n", " ").replace("\r", " ")
    ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")

    new_content, count = re.subn(
        r"^tier:\s*\S+", "tier: disabled", content, flags=re.MULTILINE
    )
    if count == 0:
        separator = "" if not content or content.endswith("\n") else "\n"
        new_content = content + separator + "tier: disabled\n"

    new_content += f"\n# ABANDONED {ts}: {sanitized}\n"
    rule_file.write_text(new_content)

    log_dir = Path(project_root) / ".vaudeville" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_entry = json.dumps(
        {"ts": ts, "rule": rule_name, "reason": sanitized, "metrics": metrics}
    )
    with open(log_dir / "abandoned.jsonl", "a") as f:
        f.write(log_entry + "\n")


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


def _build_phase_args(rule_name: str, thresholds: Thresholds) -> list[str]:
    return ["--rule_name", rule_name, *_build_threshold_args(thresholds)]


def _run_phase(
    phase_name: str,
    ralph_dir: str,
    extra_args: list[str],
    project_root: str,
    runner: _RalphRunner,
) -> subprocess.CompletedProcess[str]:
    """Run one ralph phase; raise RalphError on non-zero exit."""
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


def orchestrate_tune(
    rule_name: str,
    thresholds: Thresholds,
    rounds: int,
    tuner_iters: int,
    project_root: str,
    commands_dir: str,
    runner: _RalphRunner = default_ralph_runner,
) -> int:
    """Run designer → tuner → judge for up to `rounds` iterations."""
    design_dir = os.path.join(commands_dir, "design")
    tune_dir = os.path.join(commands_dir, "tune")
    judge_dir = os.path.join(commands_dir, "judge")
    plan_file = (
        Path(project_root) / "commands" / "tune" / "state" / f"{rule_name}.plan.md"
    )

    verdict = JudgeVerdict(kind="JUDGE_CONTINUE_RE_DESIGN")
    judge_stdout = ""

    for _round in range(rounds):
        phase_args = _build_phase_args(rule_name, thresholds)

        if verdict.kind in ("JUDGE_CONTINUE_RE_DESIGN", "JUDGE_RAISE"):
            _run_phase(
                "design", design_dir, ["-n", "1"] + phase_args, project_root, runner
            )

        if not _is_empty_plan(plan_file):
            _run_phase(
                "tune",
                tune_dir,
                ["-n", str(tuner_iters)] + phase_args,
                project_root,
                runner,
            )

        result = _run_phase(
            "judge", judge_dir, ["-n", "1"] + phase_args, project_root, runner
        )
        judge_stdout = result.stdout
        verdict = parse_judge_signal(judge_stdout)

        if verdict.kind in ("JUDGE_DONE", "JUDGE_ABANDON"):
            break
        if verdict.kind == "JUDGE_RAISE" and verdict.raised is not None:
            thresholds = verdict.raised

    if verdict.kind == "JUDGE_ABANDON":
        reason = _extract_abandon_reason(judge_stdout) or verdict.raw_line
        abandon_rule(rule_name, reason, {}, project_root)

    return 0


def _extract_abandon_reason(judge_stdout: str) -> str:
    """Return judge prose above the final JUDGE_* signal line (trimmed to 2000 chars)."""
    prose: list[str] = []
    for line in judge_stdout.splitlines():
        if line.strip().startswith("JUDGE_"):
            break
        prose.append(line)
    return "\n".join(prose).strip()[-2000:]


def _eval_rule(rule_name: str, project_root: str) -> Thresholds | None:
    """Run eval_cli for a rule; parse P/R/F1 from output. Returns None on failure."""
    try:
        out = subprocess.run(
            ["uv", "run", "python", "-m", "vaudeville.eval_cli", "--rule", rule_name],
            capture_output=True,
            text=True,
            cwd=project_root,
            check=False,
        ).stdout
    except FileNotFoundError:
        return None
    try:
        return Thresholds(
            p_min=float(re.search(r"precision=([\d.]+)", out).group(1)),  # type: ignore[union-attr]
            r_min=float(re.search(r"recall=([\d.]+)", out).group(1)),  # type: ignore[union-attr]
            f1_min=float(re.search(r"f1=([\d.]+)", out).group(1)),  # type: ignore[union-attr]
        )
    except (AttributeError, ValueError):
        return None


def _snapshot_rules(rules_dir: Path) -> set[str]:
    """Return the set of .yaml/.yml filenames currently in rules_dir."""
    if not rules_dir.exists():
        return set()
    return {f.name for f in rules_dir.iterdir() if f.suffix in (".yaml", ".yml")}


def orchestrate_generate(
    instructions: str,
    thresholds: Thresholds,
    rounds: int,
    tuner_iters: int,
    mode: str,
    project_root: str,
    commands_dir: str,
    runner: _RalphRunner = default_ralph_runner,
) -> int:
    """Run generate designer, then tune any rules that miss thresholds."""
    rules_dir = Path(project_root) / ".vaudeville" / "rules"
    before = _snapshot_rules(rules_dir)

    generate_dir = os.path.join(commands_dir, "generate")
    gen_args = [
        "-n",
        "1",
        "--instructions",
        instructions,
        *_build_threshold_args(thresholds),
        "--mode",
        mode,
    ]
    _run_phase("generate", generate_dir, gen_args, project_root, runner)

    after = _snapshot_rules(rules_dir)
    new_files = after - before
    new_rules = sorted(Path(f).stem for f in new_files)

    for name in new_rules:
        result = _eval_rule(name, project_root)
        if result is None or (
            result.p_min < thresholds.p_min
            or result.r_min < thresholds.r_min
            or result.f1_min < thresholds.f1_min
        ):
            orchestrate_tune(
                name,
                thresholds,
                rounds,
                tuner_iters,
                project_root,
                commands_dir,
                runner,
            )

    return 0
