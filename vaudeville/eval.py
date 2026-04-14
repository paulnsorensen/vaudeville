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
from dataclasses import dataclass, field

import yaml

from .core.protocol import ClassifyResult, compute_confidence, parse_verdict
from .core.rules import Rule, load_rules, load_rules_layered
from .server import InferenceBackend, LogprobBackend
from .server import condense_text


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
    misclassified: list[dict[str, str]] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)

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
    if isinstance(backend, LogprobBackend):
        return backend.classify_with_logprobs(prompt, max_tokens=50)
    text = backend.classify(prompt, max_tokens=50)
    return ClassifyResult(text=text)


def _update_results(results: EvalResults, case: EvalCase, predicted: str) -> None:
    positive, negative = "violation", "clean"
    if case.label == positive and predicted == positive:
        results.tp += 1
    elif case.label == negative and predicted == negative:
        results.tn += 1
    elif case.label == negative and predicted == positive:
        results.fp += 1
        results.misclassified.append(
            {"text": case.text, "actual": negative, "predicted": positive}
        )
    else:
        results.fn += 1
        results.misclassified.append(
            {"text": case.text, "actual": positive, "predicted": negative}
        )


def classify_case(
    case: EvalCase,
    rule: Rule,
    backend: InferenceBackend,
    results: EvalResults,
) -> CaseResult:
    """Classify a single case and update results. Returns CaseResult."""
    text = case.text
    if rule.event == "Stop" and len(text) >= 200:
        text = condense_text(text, backend)
    prompt = rule.format_prompt(text)
    result = _run_inference(backend, prompt)
    response = parse_verdict(result.text)
    predicted = response.verdict
    confidence = compute_confidence(result.logprobs, predicted)

    if predicted == "violation" and rule.threshold > 0 and confidence < rule.threshold:
        predicted = "clean"

    results.confidences.append(confidence)
    _update_results(results, case, predicted)

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

    results = EvalResults(rule=rule_name)
    case_results: list[CaseResult] = []
    for case in cases:
        case_results.append(classify_case(case, rule, backend, results))
    return results, case_results


def _build_backend(args: argparse.Namespace) -> InferenceBackend:
    """Initialize the inference backend from CLI args."""
    from .server import MLXBackend

    print(f"Loading model: {args.model}")
    return MLXBackend(args.model)


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
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
        "--calibrate",
        metavar="RULE",
        help="Calibrate threshold for a rule: sweep, pick F1-optimal, write to YAML",
    )
    parser.add_argument(
        "--eval-log",
        help="Path to JSONL file for regression tracking (appends one line per run)",
    )
    parser.add_argument(
        "--rules-dir",
        help="Load rules from this directory only (skip layered resolution)",
    )
    parser.add_argument(
        "--backend", default="mlx", choices=["mlx"], help="Inference backend"
    )
    parser.add_argument(
        "--model",
        default="mlx-community/Phi-4-mini-instruct-4bit",
        help="Model path or Hugging Face ID",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    tests_dir = os.path.join(plugin_root, "tests")

    backend = _build_backend(args)
    if args.rules_dir:
        rules = load_rules(args.rules_dir)
    else:
        rules = load_rules_layered(project_root=_find_project_root())
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

    if args.calibrate:
        from .eval_report import run_calibrate

        run_calibrate(args, rules, test_suites, backend, _find_project_root())

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}")
            sys.exit(1)

    from .eval_report import run_evaluations, threshold_sweep, write_eval_log

    passed, all_results = run_evaluations(args, rules, test_suites, backend)

    if args.eval_log and all_results:
        write_eval_log(args.eval_log, args.model, all_results)
        print(f"\nEval log appended to {args.eval_log}")

    if args.threshold_sweep:
        threshold_sweep(test_suites, rules, backend)

    print("\n" + ("ALL RULES PASS" if passed else "SOME RULES FAILED"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
