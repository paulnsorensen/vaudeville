"""Eval harness for vaudeville rules — core eval logic.

Data classes, test-case loading, and classification functions.
CLI entrypoint is in eval_cli.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.models import Model
from pydantic_evals import Case, Dataset, increment_eval_metric
from pydantic_evals.evaluators import ConfusionMatrixEvaluator, PrecisionRecallEvaluator
from pydantic_evals.reporting import EvaluationReport

from .rules import DecideRule, DecideTestCase, RewriteRule
from .server.agents.decide import decide
from .server.user_config import UserConfig

__all__ = [
    "CaseResult",
    "DecideOutcome",
    "EvalResults",
    "build_dataset",
    "classify_case",
    "evaluate_rule",
    "load_test_cases",
    "run_dataset",
]


@dataclass
class DecideOutcome:
    """A `decide` prediction, carried through a pydantic-evals `Case`/`ReportCase`."""

    outcome: str | None
    confidence: float | None = None

    def __str__(self) -> str:
        return str(self.outcome)


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


def build_dataset(rule: DecideRule) -> Dataset[str, DecideOutcome, None]:
    """Build a `Dataset` from `rule`'s inline test cases.

    Each case's `inputs` is the case text and `expected_output` carries the
    expected outcome. `ConfusionMatrixEvaluator`/`PrecisionRecallEvaluator`
    run as report evaluators once the dataset is scored.
    """
    cases = [
        Case(
            name=str(i),
            inputs=case.text,
            expected_output=DecideOutcome(outcome=case.outcome),
        )
        for i, case in enumerate(rule.test_cases)
    ]
    return Dataset(
        name=rule.name,
        cases=cases,
        report_evaluators=[
            ConfusionMatrixEvaluator(),
            PrecisionRecallEvaluator(
                score_key="confidence",
                score_from="metrics",
                positive_from="expected_output",
            ),
        ],
    )


def run_dataset(
    dataset: Dataset[str, DecideOutcome, None],
    rule: DecideRule,
    config: UserConfig,
    *,
    model_override: Model | None = None,
) -> EvaluationReport[str, DecideOutcome, None]:
    """Score `dataset` by running `decide` for `rule` over each case's text.

    Runs deterministically: no progress bar, one case at a time.
    """

    def _task(text: str) -> DecideOutcome:
        result = decide(rule, config, text, model_override=model_override)
        if result.confidence is not None:
            increment_eval_metric("confidence", result.confidence)
        return DecideOutcome(outcome=result.outcome, confidence=result.confidence)

    return dataset.evaluate_sync(_task, progress=False, max_concurrency=1)


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

    rule_for_eval = rule.model_copy(update={"test_cases": cases})
    dataset = build_dataset(rule_for_eval)
    report = run_dataset(dataset, rule_for_eval, config, model_override=model_override)
    case_by_name = {report_case.name: report_case for report_case in report.cases}

    results = EvalResults(rule=rule_name)
    case_results: list[CaseResult] = []
    for i, case in enumerate(cases):
        predicted = case_by_name[str(i)].output
        _update_results(results, rule_for_eval, case.outcome, predicted.outcome, case.text)
        if predicted.confidence is not None:
            results.confidences.append(predicted.confidence)
        case_results.append(
            CaseResult(
                rule=rule_name,
                case_id=i,
                text=case.text,
                expected=case.outcome,
                predicted=predicted.outcome,
                confidence=predicted.confidence,
            )
        )
    return results, case_results


if __name__ == "__main__":
    from .eval_cli import main

    main()
