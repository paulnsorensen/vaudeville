"""Confidence calibration for vaudeville decide rules.

Scores each case's `confidence` against whether the prediction matched the
expected outcome (`outcome_match`), the same positive signal `build_dataset`
wires into `PrecisionRecallEvaluator`. Reports ROC AUC, KS, Brier, ECE over
10 bins, a threshold sweep, and a recommended `unsure.below` value.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from pydantic_evals.evaluators import (
    KolmogorovSmirnovEvaluator,
    ReportEvaluatorContext,
    ROCAUCEvaluator,
)
from pydantic_evals.reporting import EvaluationReport, ReportCase
from pydantic_evals.reporting.analyses import ReportAnalysis, ScalarResult

if TYPE_CHECKING:
    from .eval import CaseResult

__all__ = ["Calibration", "ThresholdPoint", "calibrate", "format_calibration"]

_MIN_FULL_SAMPLE = 30
_TARGET_PRECISION = 0.95
# A recommendation needs at least this many confident cases: one correct
# confident case alone gives precision 1.0.
_MIN_CONFIDENT_CASES = 2
_ECE_BINS = 10
_SWEEP_THRESHOLDS = [round(i * 0.05, 2) for i in range(1, 20)] + [0.96, 0.97, 0.98, 0.99]


class ThresholdPoint(BaseModel):
    """Precision and unsure rate if `unsure.below` were set to `threshold`.

    The rate assumes the gate covers every outcome (no `unsure.outcomes`
    filter).
    """

    threshold: float
    confident: int
    precision: float | None
    unsure_rate: float


class Calibration(BaseModel):
    """Confidence-calibration report for one rule's eval cases.

    `n` counts every case; `scored` counts the cases with a confidence (0 in
    a summary that predates the field).
    `status` is `"n/a"` when no case carries a confidence, `"low-sample"`
    under 30 scored cases (the report includes noisy values), else `"ok"`.
    """

    status: Literal["ok", "low-sample", "n/a"]
    n: int
    scored: int = 0
    roc_auc: float | None = None
    ks: float | None = None
    brier: float | None = None
    ece: float | None = None
    sweep: list[ThresholdPoint] = []
    recommended_below: float | None = None
    unsure_rate: float | None = None


def _scored(case_results: list[CaseResult]) -> list[tuple[float, bool]]:
    """(confidence, predicted-matched-expected) pairs for confident cases."""
    return [
        (cr.confidence, cr.predicted == cr.expected)
        for cr in case_results
        if cr.confidence is not None
    ]


def _report_context(scored: list[tuple[float, bool]]) -> ReportEvaluatorContext[None, bool, None]:
    cases = [
        ReportCase[None, bool, None](
            name=str(i),
            inputs=None,
            metadata=None,
            expected_output=correct,
            output=correct,
            metrics={"confidence": score},
            attributes={},
            scores={},
            labels={},
            assertions={},
            task_duration=0.0,
            total_duration=0.0,
        )
        for i, (score, correct) in enumerate(scored)
    ]
    report = EvaluationReport[None, bool, None](name="calibration", cases=cases)
    return ReportEvaluatorContext(name="calibration", report=report, experiment_metadata=None)


def _scalar(analyses: list[ReportAnalysis]) -> float | None:
    """Value of the first `ScalarResult` in `analyses`, or None if absent or NaN."""
    result = next((a for a in analyses if isinstance(a, ScalarResult)), None)
    if result is None:
        return None
    value = float(result.value)
    return None if math.isnan(value) else value


def _roc_auc(scored: list[tuple[float, bool]]) -> float | None:
    ctx = _report_context(scored)
    evaluator = ROCAUCEvaluator(
        score_key="confidence", score_from="metrics", positive_from="expected_output"
    )
    return _scalar(evaluator.evaluate(ctx))


def _ks(scored: list[tuple[float, bool]]) -> float | None:
    ctx = _report_context(scored)
    evaluator = KolmogorovSmirnovEvaluator(
        score_key="confidence", score_from="metrics", positive_from="expected_output"
    )
    return _scalar(evaluator.evaluate(ctx))


def _brier(scored: list[tuple[float, bool]]) -> float:
    return sum((score - float(correct)) ** 2 for score, correct in scored) / len(scored)


def _ece(scored: list[tuple[float, bool]], *, bins: int = _ECE_BINS) -> float:
    total = len(scored)
    ece = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        bucket = [(s, c) for s, c in scored if s >= lo and (s < hi or i == bins - 1)]
        if not bucket:
            continue
        avg_conf = sum(s for s, _ in bucket) / len(bucket)
        avg_acc = sum(float(c) for _, c in bucket) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_conf - avg_acc)
    return ece


def _sweep(scored: list[tuple[float, bool]], total: int) -> list[ThresholdPoint]:
    points: list[ThresholdPoint] = []
    for threshold in _SWEEP_THRESHOLDS:
        confident = [correct for s, correct in scored if s >= threshold]
        precision = (
            sum(1 for correct in confident if correct) / len(confident) if confident else None
        )
        unsure_rate = (len(scored) - len(confident)) / total
        points.append(
            ThresholdPoint(
                threshold=threshold,
                confident=len(confident),
                precision=precision,
                unsure_rate=unsure_rate,
            )
        )
    return points


def _recommended(sweep: list[ThresholdPoint]) -> tuple[float, float] | None:
    """Lowest swept threshold whose confident cases reach the target precision.

    A confident set under `_MIN_CONFIDENT_CASES` never counts as reaching the
    target. Returns None when no swept threshold reaches it.
    """
    for point in sweep:
        if (
            point.confident >= _MIN_CONFIDENT_CASES
            and point.precision is not None
            and point.precision >= _TARGET_PRECISION
        ):
            return point.threshold, point.unsure_rate
    return None


def calibrate(case_results: list[CaseResult]) -> Calibration:
    """Build a `Calibration` report from one rule's scored eval cases."""
    n = len(case_results)
    scored = _scored(case_results)
    if not scored:
        return Calibration(status="n/a", n=n, scored=0)

    sweep = _sweep(scored, n)
    recommended = _recommended(sweep)
    recommended_below = recommended[0] if recommended is not None else None
    unsure_rate = recommended[1] if recommended is not None else None
    status: Literal["ok", "low-sample"] = "ok" if len(scored) >= _MIN_FULL_SAMPLE else "low-sample"
    return Calibration(
        status=status,
        n=n,
        scored=len(scored),
        roc_auc=_roc_auc(scored),
        ks=_ks(scored),
        brier=_brier(scored),
        ece=_ece(scored),
        sweep=sweep,
        recommended_below=recommended_below,
        unsure_rate=unsure_rate,
    )


def format_calibration(rule_name: str, calibration: Calibration) -> str:
    """Render `calibration` as the `--calibrate` text report."""
    lines = [f"\n=== Calibration: {rule_name} ==="]
    if calibration.status == "n/a":
        lines.append(f"n={calibration.n}: no confidences recorded; calibration is n/a")
        return "\n".join(lines)

    if calibration.status == "low-sample":
        lines.append(
            f"scored={calibration.scored} of n={calibration.n} (< {_MIN_FULL_SAMPLE}):"
            " low-sample, numbers are noisy"
        )

    lines.append(
        f"ROC AUC: {calibration.roc_auc:.3f}"
        if calibration.roc_auc is not None
        else "ROC AUC: n/a"
    )
    lines.append(
        f"KS:      {calibration.ks:.3f}" if calibration.ks is not None else "KS:      n/a"
    )
    lines.append(f"Brier:   {calibration.brier:.4f}")
    lines.append(f"ECE:     {calibration.ece:.4f}")
    lines.append("Threshold sweep:")
    for point in calibration.sweep:
        precision_str = f"{point.precision:.3f}" if point.precision is not None else "n/a"
        lines.append(
            f"  below={point.threshold:.2f} precision={precision_str}"
            f" unsure_rate={point.unsure_rate:.3f}"
        )
    if calibration.recommended_below is not None:
        lines.append(
            f"Recommended unsure.below={calibration.recommended_below:.2f}"
            f" (unsure rate {calibration.unsure_rate:.3f};"
            " assumes an unfiltered gate: an unsure.outcomes filter gates fewer cases)"
        )
    else:
        lines.append(
            f"No threshold reached the target precision ({_TARGET_PRECISION:.2f})"
            f" with enough confident cases (>= {_MIN_CONFIDENT_CASES})"
        )
    return "\n".join(lines)
