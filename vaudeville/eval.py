"""Eval harness for vaudeville rules — core eval logic.

Data classes, test-case loading, and classification functions.
CLI entrypoint is in eval_cli.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .core import ClassifyResult, EvalCase, Rule, compute_confidence, parse_verdict
from .core.protocol import CLASSIFY_MAX_TOKENS
from .server import (
    InferenceBackend,
    LogprobBackend,
    condense_text,
)

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalResults",
    "classify_case",
    "evaluate_rule",
    "load_test_cases",
]


@dataclass
class CaseResult:
    rule: str
    case_id: int
    text: str
    label: str
    predicted: str
    confidence: float

    def to_jsonl_dict(self) -> dict[str, str | int | float]:
        return {
            "rule": self.rule,
            "case_id": self.case_id,
            "expected": self.label,
            "predicted": self.predicted,
            "confidence": round(self.confidence, 4),
            "text": self.text,
        }


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


def load_test_cases(rules: dict[str, Rule]) -> dict[str, list[EvalCase]]:
    """Collect test cases declared inline on each loaded rule."""
    return {r.name: list(r.test_cases) for r in rules.values() if r.test_cases}


def _load_test_file(path: str) -> tuple[list[EvalCase], str]:
    """Load test cases from a single --test-file YAML. Returns (cases, rule_name)."""
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
        return backend.classify_with_logprobs(prompt, max_tokens=CLASSIFY_MAX_TOKENS)
    text = backend.classify(prompt, max_tokens=CLASSIFY_MAX_TOKENS)
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
    case_id: int = 0,
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
        rule=rule.name,
        case_id=case_id,
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
    for i, case in enumerate(cases):
        cr = classify_case(case, rule, backend, results, case_id=i)
        case_results.append(cr)
    return results, case_results
