"""Eval reporting for vaudeville rules: text output and the `--json` summary."""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import TYPE_CHECKING, TextIO

from pydantic import BaseModel
from pydantic_ai.models import Model

from .eval_calibrate import Calibration, calibrate
from .rules import DecideRule, RewriteRule
from .server.user_config import UserConfig

if TYPE_CHECKING:
    from .eval import CaseResult, EvalResults
    from .rules import DecideTestCase

_PRECISION_GATE = 0.95
_RECALL_GATE = 0.80


class RuleSummary(BaseModel):
    """One rule's `--json` run-summary entry."""

    rule: str
    n: int
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float
    recall: float
    f1: float
    passed: bool
    calibration: Calibration


class RunSummary(BaseModel):
    """The `--json` run summary printed as exactly one JSON object."""

    passed: bool
    rules: list[RuleSummary]

    def to_json(self) -> str:
        return self.model_dump_json()


def _passes_gate(results: EvalResults) -> bool:
    return results.precision >= _PRECISION_GATE and results.recall >= _RECALL_GATE


def print_results(results: EvalResults, *, file: TextIO | None = None) -> bool:
    """Print metrics, return True if precision >= 95% and recall >= 80%."""
    out = file if file is not None else sys.stdout
    prec_pct = results.precision * 100
    rec_pct = results.recall * 100
    prec_ok = results.precision >= _PRECISION_GATE
    rec_ok = results.recall >= _RECALL_GATE
    passed = _passes_gate(results)
    status = "PASS" if passed else "FAIL"

    def _marker(ok: bool) -> str:
        return "" if ok else " << BELOW THRESHOLD"

    print(f"\n=== {results.rule} [{status}] ===", file=out)
    print(
        f"Accuracy:  {results.accuracy * 100:.1f}% ({results.tp + results.tn}/{results.total})",
        file=out,
    )
    print(f"Precision: {prec_pct:.1f}% (>= 95%){_marker(prec_ok)}", file=out)
    print(f"Recall:    {rec_pct:.1f}% (>= 80%){_marker(rec_ok)}", file=out)
    print(f"F1:        {results.f1 * 100:.1f}%", file=out)
    print(f"Confusion: TP={results.tp} FP={results.fp} TN={results.tn} FN={results.fn}", file=out)

    if results.confidences:
        confs = results.confidences
        print(
            f"Confidence: mean={sum(confs) / len(confs):.3f}"
            f" min={min(confs):.3f} max={max(confs):.3f}",
            file=out,
        )

    if results.misclassified:
        print("\nMisclassifications:", file=out)
        for m in results.misclassified:
            print(f"  actual={m['actual']} predicted={m['predicted']}: {m['text'][:80]}", file=out)

    return passed


def run_evaluations(
    rules: dict[str, DecideRule | RewriteRule],
    test_suites: dict[str, list[DecideTestCase]],
    config: UserConfig,
    *,
    model_override: Model | None = None,
    file: TextIO | None = None,
) -> tuple[bool, dict[str, EvalResults], list[CaseResult]]:
    """Run the eval for each rule, printing text output to `file`.

    `model_override` substitutes for the resolved model on every case (for
    tests, a FunctionModel; no network call).

    Returns (all_passed, per_rule_results, all_case_results).
    """
    from .eval import evaluate_rule

    out = file if file is not None else sys.stdout
    overall_pass = True
    all_results: dict[str, EvalResults] = {}
    all_case_results: list[CaseResult] = []
    for rule_name, cases in sorted(test_suites.items()):
        if rule_name not in rules:
            print(f"\nWARNING: No rule definition found for {rule_name}", file=out)
            continue
        print(f"\nEvaluating {rule_name} ({len(cases)} cases)...", file=out)
        results, case_results = evaluate_rule(
            rule_name, cases, rules, config, model_override=model_override
        )
        all_case_results.extend(case_results)
        all_results[rule_name] = results
        if not print_results(results, file=out):
            overall_pass = False
    return overall_pass, all_results, all_case_results


def build_run_summary(
    all_results: dict[str, EvalResults], case_results: list[CaseResult]
) -> RunSummary:
    """Build the `--json` run summary, grouping cases per rule for calibration."""
    cases_by_rule: dict[str, list[CaseResult]] = defaultdict(list)
    for case in case_results:
        cases_by_rule[case.rule].append(case)

    rule_summaries: list[RuleSummary] = []
    overall_pass = True
    for rule_name, results in sorted(all_results.items()):
        rule_passed = _passes_gate(results)
        overall_pass = overall_pass and rule_passed
        rule_summaries.append(
            RuleSummary(
                rule=rule_name,
                n=results.total,
                tp=results.tp,
                fp=results.fp,
                tn=results.tn,
                fn=results.fn,
                precision=results.precision,
                recall=results.recall,
                f1=results.f1,
                passed=rule_passed,
                calibration=calibrate(cases_by_rule.get(rule_name, [])),
            )
        )
    return RunSummary(passed=overall_pass, rules=rule_summaries)
