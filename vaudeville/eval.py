"""Eval harness for vaudeville rules.

Usage:
    uv run python -m vaudeville.eval
    uv run python -m vaudeville.eval --cross-validate
    uv run python -m vaudeville.eval --rule violation-detector
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass
class EvalCase:
    text: str
    label: str


@dataclass
class EvalResults:
    rule: str
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    misclassified: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.misclassified is None:
            self.misclassified = []

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def load_test_cases(tests_dir: str) -> dict[str, list[EvalCase]]:
    suites: dict[str, list[EvalCase]] = {}
    for filename in os.listdir(tests_dir):
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue
        path = os.path.join(tests_dir, filename)
        with open(path) as f:
            data = yaml.safe_load(f)
        rule_name = str(data["rule"])
        cases = [
            EvalCase(text=str(c["text"]), label=str(c["label"]))
            for c in data.get("cases", [])
        ]
        existing = suites.get(rule_name, [])
        suites[rule_name] = existing + cases
    return suites


def _classify_case(
    case: EvalCase,
    rule: Any,
    backend: Any,
    results: EvalResults,
) -> str:
    """Classify a single case and update results. Returns predicted label."""
    from .core.protocol import parse_verdict

    prompt = rule.format_prompt(case.text)
    raw = backend.classify(prompt, max_tokens=50)
    response = parse_verdict(raw)
    predicted = response.verdict

    assert results.misclassified is not None
    if case.label == "violation" and predicted == "violation":
        results.tp += 1
    elif case.label == "clean" and predicted == "clean":
        results.tn += 1
    elif case.label == "clean" and predicted == "violation":
        results.fp += 1
        results.misclassified.append(
            {
                "text": case.text,
                "actual": "clean",
                "predicted": "violation",
            }
        )
    else:
        results.fn += 1
        results.misclassified.append(
            {
                "text": case.text,
                "actual": "violation",
                "predicted": "clean",
            }
        )

    return predicted


def evaluate_rule(
    rule_name: str,
    cases: list[EvalCase],
    rules: dict[str, Any],
    backend: Any,
    verbose: bool = False,
) -> EvalResults:
    from .core.rules import Rule

    rule = rules.get(rule_name)
    if not isinstance(rule, Rule):
        raise ValueError(f"Rule not found: {rule_name}")

    results = EvalResults(rule=rule_name, misclassified=[])

    for case in cases:
        predicted = _classify_case(case, rule, backend, results)
        if verbose:
            status = "OK" if predicted == case.label else "FAIL"
            print(
                f"  [{status}] expected={case.label} got={predicted}: {case.text[:60]}"
            )

    return results


def cross_validate_rule(
    rule_name: str,
    cases: list[EvalCase],
    rules: dict[str, Any],
    backend: Any,
) -> EvalResults:
    """Leave-one-out cross-validation: evaluate each case as its own fold."""
    from .core.rules import Rule

    rule = rules.get(rule_name)
    if not isinstance(rule, Rule):
        raise ValueError(f"Rule not found: {rule_name}")

    n = len(cases)
    aggregate = EvalResults(rule=rule_name, misclassified=[])

    for i, case in enumerate(cases):
        fold = EvalResults(rule=rule_name, misclassified=[])
        predicted = _classify_case(case, rule, backend, fold)

        # Merge fold into aggregate
        aggregate.tp += fold.tp
        aggregate.fp += fold.fp
        aggregate.tn += fold.tn
        aggregate.fn += fold.fn
        assert aggregate.misclassified is not None
        assert fold.misclassified is not None
        aggregate.misclassified.extend(fold.misclassified)

        correct = predicted == case.label
        fold_acc = 1.0 if correct else 0.0
        status = "OK" if correct else "FAIL"
        print(
            f"  Fold {i + 1}/{n} [{status}] acc={fold_acc:.0%}"
            f" expected={case.label} got={predicted}: {case.text[:50]}"
        )

    return aggregate


def print_results(results: EvalResults) -> bool:
    """Print metrics, return True if accuracy >= 90%."""
    pct = results.accuracy * 100
    passed = pct >= 90.0
    status = "PASS" if passed else "FAIL"

    print(f"\n=== {results.rule} [{status}] ===")
    print(f"Accuracy:  {pct:.1f}% ({results.tp + results.tn}/{results.total})")
    print(f"Precision: {results.precision * 100:.1f}%")
    print(f"Recall:    {results.recall * 100:.1f}%")
    print(f"F1:        {results.f1 * 100:.1f}%")
    print(f"Confusion: TP={results.tp} FP={results.fp} TN={results.tn} FN={results.fn}")

    if results.misclassified:
        print("\nMisclassifications:")
        for m in results.misclassified:
            print(
                f"  actual={m['actual']} predicted={m['predicted']}: {m['text'][:80]}"
            )

    return passed


def _load_test_file(path: str) -> list[EvalCase]:
    """Load test cases from a single YAML file."""
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f)
    return [EvalCase(text=c["text"], label=c["label"]) for c in data.get("cases", [])]


def main() -> None:
    parser = argparse.ArgumentParser(description="Vaudeville rule eval harness")
    parser.add_argument("--rule", help="Evaluate only this rule")
    parser.add_argument(
        "--cross-validate",
        action="store_true",
        help="Leave-one-out cross-validation with per-fold output",
    )
    parser.add_argument(
        "--test-file",
        help="Extra test file to include (YAML format)",
    )
    parser.add_argument(
        "--backend", default="mlx", choices=["mlx"], help="Inference backend"
    )
    parser.add_argument(
        "--model",
        default="mlx-community/Phi-3-mini-4k-instruct-4bit",
        help="Model path or Hugging Face ID",
    )
    args = parser.parse_args()

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    tests_dir = os.path.join(plugin_root, "tests")

    from .core.rules import load_rules_layered
    from .server.mlx_backend import MLXBackend

    print(f"Loading model: {args.model}")
    backend = MLXBackend(args.model)

    rules = load_rules_layered(plugin_root)
    test_suites = load_test_cases(tests_dir)

    if args.test_file and args.rule:
        extra_cases = _load_test_file(args.test_file)
        existing = test_suites.get(args.rule, [])
        test_suites[args.rule] = existing + extra_cases

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}")
            sys.exit(1)

    overall_pass = True
    for rule_name, cases in sorted(test_suites.items()):
        if rule_name not in rules:
            print(f"\nWARNING: No rule definition found for {rule_name}")
            continue
        print(f"\nEvaluating {rule_name} ({len(cases)} cases)...")
        if args.cross_validate:
            print(f"  Leave-one-out cross-validation ({len(cases)} folds):")
            results = cross_validate_rule(rule_name, cases, rules, backend)
        else:
            results = evaluate_rule(rule_name, cases, rules, backend)
        passed = print_results(results)
        if not passed:
            overall_pass = False

    print("\n" + ("ALL RULES PASS" if overall_pass else "SOME RULES FAILED"))
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
