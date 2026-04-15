"""Eval reporting, cross-validation, threshold sweep, and calibration for vaudeville rules."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from typing import TYPE_CHECKING

import yaml

from .core.rules import Rule
from .server import InferenceBackend

if TYPE_CHECKING:
    from .eval import CaseResult, EvalCase, EvalResults


def print_results(results: EvalResults) -> bool:
    """Print metrics, return True if precision >= 95% and recall >= 80%."""
    prec_pct = results.precision * 100
    rec_pct = results.recall * 100
    prec_ok = prec_pct >= 95.0
    rec_ok = rec_pct >= 80.0
    passed = prec_ok and rec_ok
    status = "PASS" if passed else "FAIL"

    def _marker(ok: bool) -> str:
        return "" if ok else " << BELOW THRESHOLD"

    print(f"\n=== {results.rule} [{status}] ===")
    print(
        f"Accuracy:  {results.accuracy * 100:.1f}% ({results.tp + results.tn}/{results.total})"
    )
    print(f"Precision: {prec_pct:.1f}% (>= 95%){_marker(prec_ok)}")
    print(f"Recall:    {rec_pct:.1f}% (>= 80%){_marker(rec_ok)}")
    print(f"F1:        {results.f1 * 100:.1f}%")
    print(f"Confusion: TP={results.tp} FP={results.fp} TN={results.tn} FN={results.fn}")

    if results.confidences:
        confs = results.confidences
        print(
            f"Confidence: mean={sum(confs) / len(confs):.3f}"
            f" min={min(confs):.3f} max={max(confs):.3f}"
        )

    if results.misclassified:
        print("\nMisclassifications:")
        for m in results.misclassified:
            print(
                f"  actual={m['actual']} predicted={m['predicted']}: {m['text'][:80]}"
            )

    return passed


def cross_validate_rule(
    rule_name: str,
    cases: list[EvalCase],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> EvalResults:
    """Leave-one-out cross-validation: evaluate each case as its own fold."""
    from .eval import EvalResults, classify_case

    rule = rules.get(rule_name)
    if not isinstance(rule, Rule):
        raise ValueError(f"Rule not found: {rule_name}")

    n = len(cases)
    aggregate = EvalResults(rule=rule_name)

    for i, case in enumerate(cases):
        fold = EvalResults(rule=rule_name)
        case_result = classify_case(case, rule, backend, fold)

        aggregate.tp += fold.tp
        aggregate.fp += fold.fp
        aggregate.tn += fold.tn
        aggregate.fn += fold.fn
        aggregate.misclassified.extend(fold.misclassified)
        aggregate.confidences.extend(fold.confidences)

        status = "OK" if case_result.predicted == case.label else "FAIL"
        acc = "100%" if case_result.predicted == case.label else "0%"
        print(
            f"  Fold {i + 1}/{n} [{status}] acc={acc}"
            f" expected={case.label} got={case_result.predicted}: {case.text[:50]}"
        )

    return aggregate


def run_evaluations(
    args: argparse.Namespace,
    rules: dict[str, Rule],
    test_suites: dict[str, list[EvalCase]],
    backend: InferenceBackend,
) -> tuple[bool, dict[str, EvalResults], list[CaseResult]]:
    """Run eval or cross-validation for each rule.

    Returns (all_passed, per_rule_results, all_case_results).
    """
    from .eval import evaluate_rule

    overall_pass = True
    all_results: dict[str, EvalResults] = {}
    all_case_results: list[CaseResult] = []
    for rule_name, cases in sorted(test_suites.items()):
        if rule_name not in rules:
            print(f"\nWARNING: No rule definition found for {rule_name}")
            continue
        print(f"\nEvaluating {rule_name} ({len(cases)} cases)...")
        if args.cross_validate:
            print(f"  Leave-one-out cross-validation ({len(cases)} folds):")
            results = cross_validate_rule(rule_name, cases, rules, backend)
        else:
            results, case_results = evaluate_rule(rule_name, cases, rules, backend)
            all_case_results.extend(case_results)
        all_results[rule_name] = results
        if not print_results(results):
            overall_pass = False
    return overall_pass, all_results, all_case_results


def threshold_sweep(
    test_suites: dict[str, list[EvalCase]],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> None:
    """Sweep thresholds and print confusion matrix per threshold."""
    from .eval import evaluate_rule

    for rule_name, cases in sorted(test_suites.items()):
        if rule_name not in rules:
            continue
        _, case_results = evaluate_rule(rule_name, cases, rules, backend)
        print(f"\n--- Threshold sweep: {rule_name} ---")
        print(f"{'Thresh':>7} {'Acc':>6} {'Prec':>6} {'Rec':>6} {'F1':>6}")
        best_f1 = 0.0
        best_thresh = 0.0
        for pct in range(30, 95, 5):
            thresh = pct / 100.0
            r = _score_at_threshold(rule_name, case_results, thresh)
            marker = ""
            if r.f1 > best_f1 and r.precision >= 0.95:
                best_f1 = r.f1
                best_thresh = thresh
                marker = " <-- best"
            print(
                f"  {thresh:.2f}  {r.accuracy * 100:5.1f}%"
                f" {r.precision * 100:5.1f}% {r.recall * 100:5.1f}%"
                f" {r.f1 * 100:5.1f}%{marker}"
            )
        if best_thresh > 0:
            print(f"Best threshold: {best_thresh:.2f} (F1={best_f1 * 100:.1f}%)")


def _git_head() -> str:
    """Return short git HEAD hash, or 'unknown' if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def write_eval_log(
    log_path: str,
    model: str,
    results: dict[str, EvalResults],
) -> None:
    """Append one JSONL line with per-rule metrics."""
    rules_data: dict[str, dict[str, float]] = {}
    for rule_name, r in sorted(results.items()):
        rules_data[rule_name] = {
            "precision": round(r.precision, 4),
            "recall": round(r.recall, 4),
            "f1": round(r.f1, 4),
        }
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "git_head": _git_head(),
        "rules": rules_data,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _score_at_threshold(
    rule_name: str,
    case_results: list[CaseResult],
    thresh: float,
) -> EvalResults:
    """Score case results at a given confidence threshold."""
    from .eval import EvalResults

    r = EvalResults(rule=rule_name)
    for cr in case_results:
        predicted = cr.predicted
        if predicted == "violation" and cr.confidence < thresh:
            predicted = "clean"
        if cr.label == "violation" and predicted == "violation":
            r.tp += 1
        elif cr.label == "clean" and predicted == "clean":
            r.tn += 1
        elif cr.label == "clean" and predicted == "violation":
            r.fp += 1
        else:
            r.fn += 1
    return r


def _find_best_threshold(
    rule_name: str,
    case_results: list[CaseResult],
) -> tuple[float, float]:
    """Find threshold with best F1 at >= 95% precision. Returns (thresh, f1)."""
    best_f1 = 0.0
    best_thresh = 0.0
    for pct in range(30, 95, 5):
        thresh = pct / 100.0
        r = _score_at_threshold(rule_name, case_results, thresh)
        if r.f1 > best_f1 and r.precision >= 0.95:
            best_f1 = r.f1
            best_thresh = thresh
    return best_thresh, best_f1


def find_rule_file(rule_name: str, search_dirs: list[str]) -> str | None:
    """Find the YAML file for a rule by name across search directories."""
    for d in search_dirs:
        try:
            for filename in os.listdir(d):
                match = _check_file_for_rule(os.path.join(d, filename), rule_name)
                if match:
                    return match
        except OSError:
            continue
    return None


def _check_file_for_rule(path: str, rule_name: str) -> str | None:
    """Return path if the file is a YAML rule matching rule_name, else None."""
    if not (path.endswith(".yaml") or path.endswith(".yml")):
        return None
    with open(path) as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict) and data.get("name") == rule_name:
        return path
    return None


def run_calibrate(
    args: argparse.Namespace,
    rules: dict[str, Rule],
    test_suites: dict[str, list["EvalCase"]],
    backend: InferenceBackend,
    project_root: str | None,
) -> None:
    """Run --calibrate subcommand and exit."""
    import sys

    from .core.rules import rules_search_path

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


MIN_CALIBRATION_CASES = 20


@dataclass
class CalibrateTarget:
    """Bundle identifying a rule to calibrate and where its YAML lives."""

    rule_name: str
    rule_file: str


def calibrate_rule(
    target: CalibrateTarget,
    cases: list[EvalCase],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> float | None:
    """Run threshold sweep, pick F1-optimal, write to rule YAML. Returns threshold or None."""
    rule_name = target.rule_name
    rule_file = target.rule_file
    rule = rules[rule_name]
    if len(cases) < MIN_CALIBRATION_CASES:
        print(
            f"ERROR: {rule_name} has {len(cases)} labeled cases"
            f" (minimum {MIN_CALIBRATION_CASES}). Refusing to calibrate."
        )
        return None

    from .eval import evaluate_rule

    _, case_results = evaluate_rule(rule_name, cases, {rule_name: rule}, backend)
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
