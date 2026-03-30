"""Eval harness for vaudeville rules.

Usage:
    uv run python -m vaudeville.eval
    uv run python -m vaudeville.eval --cross-validate
    uv run python -m vaudeville.eval --rule violation-detector
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass

import yaml

from .core.protocol import ClassifyResult, compute_confidence, parse_verdict
from .core.rules import Rule, load_rules_layered
from .server import InferenceBackend


def _find_project_root() -> str | None:
    """Find the git working tree root, or None if not in a repo."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


@dataclass
class EvalCase:
    text: str
    label: str


@dataclass
class CaseResult:
    text: str
    label: str
    predicted: str
    confidence: float


@dataclass
class EvalResults:
    rule: str
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    misclassified: list[dict[str, str]] | None = None
    confidences: list[float] | None = None

    def __post_init__(self) -> None:
        if self.misclassified is None:
            self.misclassified = []
        if self.confidences is None:
            self.confidences = []

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
    try:
        filenames = os.listdir(tests_dir)
    except OSError:
        return suites

    for filename in filenames:
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue
        path = os.path.join(tests_dir, filename)
        try:
            cases, rule_name = _load_test_file(path)
            existing = suites.get(rule_name, [])
            suites[rule_name] = existing + cases
        except Exception as exc:
            logging.warning(
                "[vaudeville] Failed to load test file %s: %s", filename, exc
            )

    return suites


def _load_test_file(path: str) -> tuple[list[EvalCase], str]:
    """Load test cases from a single YAML file. Returns (cases, rule_name)."""
    with open(path) as f:
        data = yaml.safe_load(f)
    rule_name = str(data["rule"])
    cases = [
        EvalCase(text=str(c["text"]), label=str(c["label"]))
        for c in data.get("cases", [])
    ]
    return cases, rule_name


def _run_inference(backend: InferenceBackend, prompt: str) -> ClassifyResult:
    """Run inference with logprobs, falling back to plain classify."""
    try:
        return backend.classify_with_logprobs(prompt, max_tokens=50)
    except AttributeError:
        text = backend.classify(prompt, max_tokens=50)
        return ClassifyResult(text=text)


def _classify_case(
    case: EvalCase,
    rule: Rule,
    backend: InferenceBackend,
    results: EvalResults,
) -> CaseResult:
    """Classify a single case and update results. Returns CaseResult."""
    prompt = rule.format_prompt(case.text)
    result = _run_inference(backend, prompt)
    response = parse_verdict(result.text)
    predicted = response.verdict
    confidence = compute_confidence(result.logprobs, predicted)

    assert results.misclassified is not None
    assert results.confidences is not None
    results.confidences.append(confidence)

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

    return CaseResult(
        text=case.text,
        label=case.label,
        predicted=predicted,
        confidence=confidence,
    )


def evaluate_rule(
    rule_name: str,
    cases: list[EvalCase],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> tuple[EvalResults, list[CaseResult]]:
    rule = rules.get(rule_name)
    if not isinstance(rule, Rule):
        raise ValueError(f"Rule not found: {rule_name}")

    results = EvalResults(rule=rule_name, misclassified=[])
    case_results: list[CaseResult] = []
    for case in cases:
        case_results.append(_classify_case(case, rule, backend, results))
    return results, case_results


def cross_validate_rule(
    rule_name: str,
    cases: list[EvalCase],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> EvalResults:
    """Leave-one-out cross-validation: evaluate each case as its own fold."""
    rule = rules.get(rule_name)
    if not isinstance(rule, Rule):
        raise ValueError(f"Rule not found: {rule_name}")

    n = len(cases)
    aggregate = EvalResults(rule=rule_name, misclassified=[])

    for i, case in enumerate(cases):
        fold = EvalResults(rule=rule_name, misclassified=[])
        case_result = _classify_case(case, rule, backend, fold)

        aggregate.tp += fold.tp
        aggregate.fp += fold.fp
        aggregate.tn += fold.tn
        aggregate.fn += fold.fn
        assert aggregate.misclassified is not None
        assert aggregate.confidences is not None
        assert fold.misclassified is not None
        assert fold.confidences is not None
        aggregate.misclassified.extend(fold.misclassified)
        aggregate.confidences.extend(fold.confidences)

        status = "OK" if case_result.predicted == case.label else "FAIL"
        acc = "100%" if case_result.predicted == case.label else "0%"
        print(
            f"  Fold {i + 1}/{n} [{status}] acc={acc}"
            f" expected={case.label} got={case_result.predicted}: {case.text[:50]}"
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


def _build_backend(args: argparse.Namespace) -> InferenceBackend:
    """Initialize the inference backend from CLI args."""
    from .server import MLXBackend

    print(f"Loading model: {args.model}")
    return MLXBackend(args.model)


def _run_evaluations(
    args: argparse.Namespace,
    rules: dict[str, Rule],
    test_suites: dict[str, list[EvalCase]],
    backend: InferenceBackend,
) -> bool:
    """Run eval or cross-validation for each rule. Returns True if all pass."""
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
            results, _ = evaluate_rule(rule_name, cases, rules, backend)
        if not print_results(results):
            overall_pass = False
    return overall_pass


def _threshold_sweep(
    test_suites: dict[str, list[EvalCase]],
    rules: dict[str, Rule],
    backend: InferenceBackend,
) -> None:
    """Sweep thresholds and print confusion matrix per threshold."""
    for rule_name, cases in sorted(test_suites.items()):
        if rule_name not in rules:
            continue
        _, case_results = evaluate_rule(rule_name, cases, rules, backend)
        print(f"\n--- Threshold sweep: {rule_name} ---")
        print(f"{'Thresh':>7} {'Acc':>6} {'Prec':>6} {'Rec':>6} {'F1':>6}")
        best_f1 = 0.0
        best_thresh = 0.0
        step = 5
        for pct in range(30, 95, step):
            thresh = pct / 100.0
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
        "--threshold-sweep",
        action="store_true",
        help="Sweep thresholds 0.30-0.90 and print confusion matrix per threshold",
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

    backend = _build_backend(args)
    rules = load_rules_layered(plugin_root, project_root=_find_project_root())
    test_suites = load_test_cases(tests_dir)

    if args.test_file and args.rule:
        extra_cases, rule_name = _load_test_file(args.test_file)
        if rule_name != args.rule:
            print(
                f"Error: --test-file specifies rule '{rule_name}' but --rule is '{args.rule}'"
            )
            sys.exit(1)
        existing = test_suites.get(args.rule, [])
        test_suites[args.rule] = existing + extra_cases

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}")
            sys.exit(1)

    passed = _run_evaluations(args, rules, test_suites, backend)

    if args.threshold_sweep:
        _threshold_sweep(test_suites, rules, backend)

    print("\n" + ("ALL RULES PASS" if passed else "SOME RULES FAILED"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
