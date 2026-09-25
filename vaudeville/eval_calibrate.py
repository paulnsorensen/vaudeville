"""Confidence calibration for vaudeville decide rules.

Scores each case's `confidence` against whether the prediction matched the
expected outcome (`outcome_match`), the same positive signal `build_dataset`
wires into `PrecisionRecallEvaluator`. Reports ROC AUC, KS, Brier, ECE over
10 bins, a threshold sweep, and a recommended `unsure.below` value.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from .eval import CaseResult

__all__ = ["Calibration", "ThresholdPoint", "calibrate", "format_calibration"]

_MIN_FULL_SAMPLE = 30
_TARGET_PRECISION = 0.95
_ECE_BINS = 10
_SWEEP_THRESHOLDS = [round(i * 0.05, 2) for i in range(20)]  # 0.00 .. 0.95


class ThresholdPoint(BaseModel):
    """Precision and unsure rate if `unsure.below` were set to `threshold`."""

    threshold: float
    precision: float
    unsure_rate: float


class Calibration(BaseModel):
    """Confidence-calibration report for one rule's eval cases.

    `status` is `"n/a"` when no case carries a confidence, `"low-sample"`
    under 30 cases (the numbers are still reported, but noisy), else `"ok"`.
    """

    status: Literal["ok", "low-sample", "n/a"]
    n: int
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


def _trapezoidal_auc(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        area += (x1 - x0) * (y0 + y1) / 2
    return area


def _roc_auc(scored: list[tuple[float, bool]]) -> float | None:
    positives = sum(1 for _, correct in scored if correct)
    negatives = len(scored) - positives
    if positives == 0 or negatives == 0:
        return None
    thresholds = sorted({score for score, _ in scored}, reverse=True)
    points = [(0.0, 0.0)]
    for threshold in thresholds:
        tp = sum(1 for s, correct in scored if s >= threshold and correct)
        fp = sum(1 for s, correct in scored if s >= threshold and not correct)
        points.append((fp / negatives, tp / positives))
    points.sort()
    return _trapezoidal_auc(points)


def _ks(scored: list[tuple[float, bool]]) -> float | None:
    pos = sorted(s for s, correct in scored if correct)
    neg = sorted(s for s, correct in scored if not correct)
    if not pos or not neg:
        return None
    all_scores = sorted({s for s, _ in scored})
    return max(
        abs(bisect_right(pos, s) / len(pos) - bisect_right(neg, s) / len(neg)) for s in all_scores
    )


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


def _sweep(scored: list[tuple[float, bool]]) -> list[ThresholdPoint]:
    total = len(scored)
    points: list[ThresholdPoint] = []
    for threshold in _SWEEP_THRESHOLDS:
        confident = [correct for s, correct in scored if s >= threshold]
        precision = (
            sum(1 for correct in confident if correct) / len(confident) if confident else 1.0
        )
        unsure_rate = 1 - len(confident) / total
        points.append(
            ThresholdPoint(threshold=threshold, precision=precision, unsure_rate=unsure_rate)
        )
    return points


def _recommended(sweep: list[ThresholdPoint]) -> tuple[float, float]:
    """Lowest swept threshold whose confident cases reach the target precision.

    Falls back to the highest swept threshold (mark everything unsure) when
    no threshold clears the bar.
    """
    for point in sweep:
        if point.precision >= _TARGET_PRECISION:
            return point.threshold, point.unsure_rate
    last = sweep[-1]
    return last.threshold, last.unsure_rate


def calibrate(case_results: list[CaseResult]) -> Calibration:
    """Build a `Calibration` report from one rule's scored eval cases."""
    n = len(case_results)
    scored = _scored(case_results)
    if not scored:
        return Calibration(status="n/a", n=n)

    sweep = _sweep(scored)
    recommended_below, unsure_rate = _recommended(sweep)
    status: Literal["ok", "low-sample"] = "ok" if n >= _MIN_FULL_SAMPLE else "low-sample"
    return Calibration(
        status=status,
        n=n,
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
        lines.append(f"n={calibration.n} (< {_MIN_FULL_SAMPLE}): low-sample, numbers are noisy")

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
        lines.append(
            f"  below={point.threshold:.2f} precision={point.precision:.3f}"
            f" unsure_rate={point.unsure_rate:.3f}"
        )
    lines.append(
        f"Recommended unsure.below={calibration.recommended_below:.2f}"
        f" (unsure rate {calibration.unsure_rate:.3f})"
    )
    return "\n".join(lines)
