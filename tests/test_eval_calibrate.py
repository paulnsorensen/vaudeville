"""Tests for vaudeville/eval_calibrate.py — the AC-10 calibration contract."""

from __future__ import annotations

import pytest

from vaudeville.eval import CaseResult
from vaudeville.eval_calibrate import calibrate


def _cases(n_correct: int, n_incorrect: int) -> list[CaseResult]:
    """`n_correct` cases at confidence 0.9, `n_incorrect` at confidence 0.3.

    Confidence perfectly separates correct from incorrect predictions, so
    ROC AUC and KS are exactly 1.0 and the math is hand-verifiable.
    """
    cases = [
        CaseResult(
            rule="r", case_id=i, text="t", expected="clean", predicted="clean", confidence=0.9
        )
        for i in range(n_correct)
    ]
    cases += [
        CaseResult(
            rule="r",
            case_id=n_correct + i,
            text="t",
            expected="clean",
            predicted="violation",
            confidence=0.3,
        )
        for i in range(n_incorrect)
    ]
    return cases


class TestCalibrateFullSample:
    def test_calibrate_full_sample(self) -> None:
        result = calibrate(_cases(20, 20))

        assert result.status == "ok"
        assert result.n == 40
        assert result.roc_auc == 1.0
        assert result.ks == 1.0
        assert result.brier == pytest.approx(0.05)
        assert result.ece == pytest.approx(0.2)
        assert len(result.sweep) == 20
        assert result.recommended_below == 0.35
        assert result.unsure_rate == 0.5


class TestCalibrateLowSample:
    def test_calibrate_low_sample(self) -> None:
        result = calibrate(_cases(5, 5))

        assert result.status == "low-sample"
        assert result.n == 10
        # Same ratio as the full-sample fixture: the numbers still compute,
        # just marked noisy.
        assert result.roc_auc == 1.0
        assert result.ks == 1.0
        assert result.brier == pytest.approx(0.05)
        assert result.ece == pytest.approx(0.2)
        assert result.recommended_below == 0.35
        assert result.unsure_rate == 0.5


class TestCalibrateNoConfidence:
    def test_calibrate_no_confidence(self) -> None:
        cases = [
            CaseResult(
                rule="r", case_id=i, text="t", expected="clean", predicted="clean", confidence=None
            )
            for i in range(5)
        ]

        result = calibrate(cases)

        assert result.status == "n/a"
        assert result.n == 5
        assert result.roc_auc is None
        assert result.ks is None
        assert result.brier is None
        assert result.ece is None
        assert result.sweep == []
        assert result.recommended_below is None
        assert result.unsure_rate is None
