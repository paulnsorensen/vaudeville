"""Eval reporting, cross-validation, and threshold sweep for vaudeville rules."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone

from typing import TYPE_CHECKING

from .core import Rule
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
        case_result = classify_case(case, rule, backend, fold, case_id=i)

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
            r = score_at_threshold(rule_name, case_results, thresh)
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


def score_at_threshold(
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
