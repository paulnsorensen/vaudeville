"""Threshold calibration for vaudeville rules.

Sweeps thresholds, picks F1-optimal at >= 95% precision, and writes
the result back to the rule YAML file.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from .core import Rule, rules_search_path
from .eval_report import score_at_threshold
from .server import InferenceBackend

if TYPE_CHECKING:
    from .eval import CaseResult, EvalCase


MIN_CALIBRATION_CASES = 20


@dataclass
class CalibrateTarget:
    """Bundle identifying a rule to calibrate and where its YAML lives."""

    rule_name: str
    rule_file: str


def _find_best_threshold(
    rule_name: str,
    case_results: list[CaseResult],
) -> tuple[float, float]:
    """Find threshold with best F1 at >= 95% precision. Returns (thresh, f1)."""
    best_f1 = 0.0
    best_thresh = 0.0
    for pct in range(30, 95, 5):
        thresh = pct / 100.0
        r = score_at_threshold(rule_name, case_results, thresh)
        if r.f1 > best_f1 and r.precision >= 0.95:
            best_f1 = r.f1
            best_thresh = thresh
    return best_thresh, best_f1


def find_rule_file(rule_name: str, search_dirs: list[str]) -> str | None:
    """Find the YAML file for a rule by name across search directories."""
    for d in search_dirs:
        match = _scan_dir(d, rule_name)
        if match:
            return match
    return None


def _scan_dir(directory: str, rule_name: str) -> str | None:
    try:
        filenames = os.listdir(directory)
    except OSError:
        return None
    for filename in filenames:
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue
        path = os.path.join(directory, filename)
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and data.get("name") == rule_name:
            return path
    return None


def calibrate_rule(
    target: CalibrateTarget,
    cases: list[EvalCase],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> float | None:
    """Run threshold sweep, pick F1-optimal, write to rule YAML. Returns threshold or None."""
    rule_name = target.rule_name
    rule_file = target.rule_file
    if len(cases) < MIN_CALIBRATION_CASES:
        print(
            f"ERROR: {rule_name} has {len(cases)} labeled cases"
            f" (minimum {MIN_CALIBRATION_CASES}). Refusing to calibrate."
        )
        return None

    from .eval import evaluate_rule

    _, case_results = evaluate_rule(rule_name, cases, rules, backend)
    best_thresh, best_f1 = _find_best_threshold(rule_name, case_results)

    if best_thresh == 0.0:
        print(f"WARNING: No threshold achieves >= 95% precision for {rule_name}.")
        return None

    with open(rule_file) as f:
        data = yaml.safe_load(f)
    data["threshold"] = best_thresh
    with open(rule_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    print(
        f"Calibrated {rule_name}: threshold={best_thresh:.2f} (F1={best_f1 * 100:.1f}%)"
    )
    print(f"Updated {rule_file}")
    return best_thresh


def run_calibrate(
    args: argparse.Namespace,
    rules: dict[str, Rule],
    test_suites: dict[str, list[EvalCase]],
    backend: InferenceBackend,
    project_root: str | None,
) -> None:
    """Run --calibrate subcommand and exit."""
    cal_rule = args.calibrate
    if cal_rule not in test_suites:
        print(f"No test suite found for rule: {cal_rule}")
        sys.exit(1)
    if cal_rule not in rules:
        print(f"Rule not found: {cal_rule}")
        sys.exit(1)

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if args.rules_dir:
        search_dirs = [args.rules_dir]
    else:
        search_dirs = rules_search_path(project_root=project_root)
        for subdir in ("rules", "examples/rules"):
            d = os.path.join(plugin_root, subdir)
            if os.path.isdir(d) and d not in search_dirs:
                search_dirs.append(d)
    rule_file = find_rule_file(cal_rule, search_dirs)
    if not rule_file:
        print(f"Cannot find YAML file for rule: {cal_rule}")
        sys.exit(1)
    target = CalibrateTarget(rule_name=cal_rule, rule_file=rule_file)
    result = calibrate_rule(target, test_suites[cal_rule], rules, backend)
    sys.exit(0 if result is not None else 1)
