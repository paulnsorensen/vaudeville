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
        assert len(result.sweep) == 23
        assert result.recommended_below == 0.35
        assert 0 < result.recommended_below <= 1
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


class TestRecommendedBelow:
    """Review findings A, B, C: `recommended_below` must be a valid,
    reachable `unsure.below`, never a silent fallback."""

    def test_case_a_uniform_confidence_recommends_lowest_swept_threshold(self) -> None:
        """39/40 correct, one uniform confidence: P=0.975 at every threshold
        that has a non-empty confident set, so the lowest one is recommended.
        """
        cases = [
            CaseResult(
                rule="r", case_id=i, text="t", expected="clean", predicted="clean", confidence=0.5
            )
            for i in range(39)
        ]
        cases.append(
            CaseResult(
                rule="r",
                case_id=39,
                text="t",
                expected="clean",
                predicted="violation",
                confidence=0.5,
            )
        )

        result = calibrate(cases)

        assert result.recommended_below == 0.05
        assert 0 < result.recommended_below <= 1
        assert result.unsure_rate == 0.0

    def test_case_b_overconfident_rule_returns_none(self) -> None:
        """All confidences at 0.99, 80% correct: precision never reaches 0.95
        at any swept threshold, so there is no silent fallback recommendation.
        """
        cases = [
            CaseResult(
                rule="r", case_id=i, text="t", expected="clean", predicted="clean", confidence=0.99
            )
            for i in range(32)
        ]
        cases += [
            CaseResult(
                rule="r",
                case_id=32 + i,
                text="t",
                expected="clean",
                predicted="violation",
                confidence=0.99,
            )
            for i in range(8)
        ]

        result = calibrate(cases)

        assert result.recommended_below is None
        assert result.unsure_rate is None

    def test_case_c_empty_confident_set_does_not_meet_target(self) -> None:
        """All confidences at 0.6, 70% correct: thresholds above 0.6 have an
        empty confident set and must not be mistaken for reaching the target.
        """
        cases = [
            CaseResult(
                rule="r", case_id=i, text="t", expected="clean", predicted="clean", confidence=0.6
            )
            for i in range(7)
        ]
        cases += [
            CaseResult(
                rule="r",
                case_id=7 + i,
                text="t",
                expected="clean",
                predicted="violation",
                confidence=0.6,
            )
            for i in range(3)
        ]

        result = calibrate(cases)

        assert result.recommended_below is None
        assert result.unsure_rate is None


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
