"""Eval reporting and cross-validation for vaudeville rules."""

from __future__ import annotations

import argparse

from typing import TYPE_CHECKING

from pydantic_ai.models import Model

from .rules import DecideRule, RewriteRule
from .server.user_config import UserConfig

if TYPE_CHECKING:
    from .eval import CaseResult, EvalResults
    from .rules import DecideTestCase


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
    cases: list[DecideTestCase],
    rules: dict[str, DecideRule | RewriteRule],
    config: UserConfig,
    *,
    model_override: Model | None = None,
) -> EvalResults:
    """Leave-one-out cross-validation: evaluate each case as its own fold.

    `model_override` substitutes for the resolved model on every fold (for
    tests, a FunctionModel; no network call).
    """
    from .eval import EvalResults, classify_case

    rule = rules.get(rule_name)
    if not isinstance(rule, DecideRule):
        raise ValueError(f"Rule not found: {rule_name}")

    n = len(cases)
    aggregate = EvalResults(rule=rule_name)

    for i, case in enumerate(cases):
        fold = EvalResults(rule=rule_name)
        case_result = classify_case(
            case, rule, config, fold, case_id=i, model_override=model_override
        )

        aggregate.tp += fold.tp
        aggregate.fp += fold.fp
        aggregate.tn += fold.tn
        aggregate.fn += fold.fn
        aggregate.misclassified.extend(fold.misclassified)
        aggregate.confidences.extend(fold.confidences)

        status = "OK" if case_result.predicted == case.outcome else "FAIL"
        acc = "100%" if case_result.predicted == case.outcome else "0%"
        print(
            f"  Fold {i + 1}/{n} [{status}] acc={acc}"
            f" expected={case.outcome} got={case_result.predicted}: {case.text[:50]}"
        )

    return aggregate


def run_evaluations(
    args: argparse.Namespace,
    rules: dict[str, DecideRule | RewriteRule],
    test_suites: dict[str, list[DecideTestCase]],
    config: UserConfig,
    *,
    model_override: Model | None = None,
) -> tuple[bool, dict[str, EvalResults], list[CaseResult]]:
    """Run eval or cross-validation for each rule.

    `model_override` substitutes for the resolved model on every case (for
    tests, a FunctionModel; no network call).

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
            results = cross_validate_rule(
                rule_name, cases, rules, config, model_override=model_override
            )
        else:
            results, case_results = evaluate_rule(
                rule_name, cases, rules, config, model_override=model_override
            )
            all_case_results.extend(case_results)
        all_results[rule_name] = results
        if not print_results(results):
            overall_pass = False
    return overall_pass, all_results, all_case_results
