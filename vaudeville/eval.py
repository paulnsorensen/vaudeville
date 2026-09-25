"""Eval harness for vaudeville rules — core eval logic.

Data classes, test-case loading, and classification functions.
CLI entrypoint is in eval_cli.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.models import Model

from .rules import DecideRule, DecideTestCase, RewriteRule
from .server.agents.decide import decide
from .server.user_config import UserConfig

__all__ = [
    "CaseResult",
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
    expected: str
    predicted: str | None
    confidence: float | None


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


def load_test_cases(
    rules: dict[str, DecideRule | RewriteRule],
) -> dict[str, list[DecideTestCase]]:
    """Collect test cases declared inline on each loaded decide rule."""
    return {
        name: list(rule.test_cases)
        for name, rule in rules.items()
        if isinstance(rule, DecideRule) and rule.test_cases
    }


def _positive_outcomes(rule: DecideRule) -> set[str]:
    """Outcomes whose `on:` action is not allow; falls back to `outcomes[0]`.

    The eval harness scores a rule against its blocking outcomes, not
    outcome list order: an outcome with no `on:` entry, or an `on:`
    entry mapped to `allow`, fails open and is not a positive.
    """
    positives = {
        outcome
        for outcome in rule.outcomes
        if rule.on.get(outcome) is not None and rule.on[outcome].action != "allow"
    }
    return positives if positives else {rule.outcomes[0]}


def _update_results(
    results: EvalResults,
    rule: DecideRule,
    expected: str,
    predicted_effective: str | None,
    text: str,
) -> None:
    positives = _positive_outcomes(rule)
    expected_positive = expected in positives
    predicted_positive = predicted_effective in positives

    if expected_positive and predicted_positive:
        results.tp += 1
    elif expected_positive and not predicted_positive:
        results.fn += 1
        results.misclassified.append(
            {"text": text, "actual": expected, "predicted": str(predicted_effective)}
        )
    elif not expected_positive and predicted_positive:
        results.fp += 1
        results.misclassified.append(
            {"text": text, "actual": expected, "predicted": str(predicted_effective)}
        )
    elif expected == predicted_effective or predicted_effective is None:
        # A fail-open `None` prediction is not a specific wrong label; it
        # still counts as a correct non-block outcome.
        results.tn += 1
    else:
        # Both outcomes are non-positive but disagree (for example
        # `ticket-instead` vs `clean`): a real mislabel, but not a false
        # negative for the blocking outcome, so it is not counted as tn.
        results.misclassified.append(
            {"text": text, "actual": expected, "predicted": str(predicted_effective)}
        )


def classify_case(
    case: DecideTestCase,
    rule: DecideRule,
    config: UserConfig,
    results: EvalResults,
    case_id: int = 0,
    *,
    model_override: Model | None = None,
) -> CaseResult:
    """Classify a single case and update results. Returns CaseResult.

    A fail-open `DecideResult.outcome is None` counts as predicted-negative.
    """
    result = decide(rule, config, case.text, model_override=model_override)
    predicted = result.outcome

    _update_results(results, rule, case.outcome, predicted, case.text)
    if result.confidence is not None:
        results.confidences.append(result.confidence)

    return CaseResult(
        rule=rule.name,
        case_id=case_id,
        text=case.text,
        expected=case.outcome,
        predicted=predicted,
        confidence=result.confidence,
    )


def evaluate_rule(
    rule_name: str,
    cases: list[DecideTestCase],
    rules: dict[str, DecideRule | RewriteRule],
    config: UserConfig,
    *,
    model_override: Model | None = None,
) -> tuple[EvalResults, list[CaseResult]]:
    rule = rules.get(rule_name)
    if not isinstance(rule, DecideRule):
        raise ValueError(f"Rule not found: {rule_name}")

    results = EvalResults(rule=rule_name)
    case_results: list[CaseResult] = []
    for i, case in enumerate(cases):
        cr = classify_case(case, rule, config, results, case_id=i, model_override=model_override)
        case_results.append(cr)
    return results, case_results


if __name__ == "__main__":
    from .eval_cli import main

    main()
