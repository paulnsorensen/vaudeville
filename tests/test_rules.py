"""Tests for rule loading and eval harness mechanics."""

from __future__ import annotations

import os

import pytest

from vaudeville.core.rules import Rule, get_draft_rule_names
from vaudeville.eval import (
    EvalCase,
    EvalResults,
    evaluate_rule,
    load_test_cases,
)
from vaudeville.eval_report import print_results
from conftest import PROJECT_ROOT, MockBackend


class TestLoadTestCases:
    def test_loads_all_suites(self, tests_dir: str) -> None:
        suites = load_test_cases(tests_dir)
        assert "violation-detector" in suites
        assert "dismissal-detector" in suites
        assert "deferral-detector" in suites

    def test_cases_have_text_and_label(self, tests_dir: str) -> None:
        suites = load_test_cases(tests_dir)
        for case in suites["violation-detector"]:
            assert isinstance(case.text, str)
            assert case.label in ("violation", "clean")

    def test_sufficient_cases_per_rule(self, tests_dir: str) -> None:
        suites = load_test_cases(tests_dir)
        drafts = get_draft_rule_names(os.path.join(PROJECT_ROOT, "examples", "rules"))
        for rule_name, cases in suites.items():
            minimum = 5 if rule_name in drafts else 10
            assert len(cases) >= minimum, (
                f"{rule_name}: only {len(cases)} test cases (need {minimum})"
            )

    def test_both_labels_present(self, tests_dir: str) -> None:
        suites = load_test_cases(tests_dir)
        for rule_name, cases in suites.items():
            labels = {c.label for c in cases}
            assert "violation" in labels, f"{rule_name}: no violation cases"
            assert "clean" in labels, f"{rule_name}: no clean cases"


class TestEvalResults:
    def test_accuracy_calculation(self) -> None:
        r = EvalResults(rule="test", tp=8, tn=9, fp=1, fn=2)
        assert abs(r.accuracy - 17 / 20) < 0.001

    def test_precision_calculation(self) -> None:
        r = EvalResults(rule="test", tp=8, fp=2, tn=0, fn=0)
        assert abs(r.precision - 0.8) < 0.001

    def test_recall_calculation(self) -> None:
        r = EvalResults(rule="test", tp=8, fn=2, tn=0, fp=0)
        assert abs(r.recall - 0.8) < 0.001

    def test_f1_harmonic_mean(self) -> None:
        r = EvalResults(rule="test", tp=8, fp=2, tn=8, fn=2)
        assert abs(r.f1 - 0.8) < 0.001

    def test_zero_division_safety(self) -> None:
        r = EvalResults(rule="test")
        assert r.accuracy == 0.0
        assert r.precision == 0.0
        assert r.recall == 0.0
        assert r.f1 == 0.0


class TestEvaluateRule:
    @pytest.fixture
    def rules(self) -> dict[str, Rule]:
        return {
            "violation-detector": Rule(
                name="violation-detector",
                event="Stop",
                prompt="Classify:\n{text}\nVERDICT:",
                context=[{"field": "last_assistant_message"}],
                action="block",
                message="{reason}",
            ),
        }

    def test_perfect_accuracy(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="violation")
        cases = [EvalCase(text="test", label="violation")] * 5
        results, _ = evaluate_rule("violation-detector", cases, rules, backend)
        assert results.accuracy == 1.0
        assert results.tp == 5

    def test_all_false_positives(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="violation")
        cases = [EvalCase(text="test", label="clean")] * 5
        results, _ = evaluate_rule("violation-detector", cases, rules, backend)
        assert results.fp == 5
        assert results.accuracy == 0.0

    def test_unknown_rule_raises(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend()
        with pytest.raises(ValueError, match="not found"):
            evaluate_rule("nonexistent", [], rules, backend)

    def test_misclassifications_recorded(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="violation")
        cases = [EvalCase(text="clean text", label="clean")]
        results, _ = evaluate_rule("violation-detector", cases, rules, backend)
        assert len(results.misclassified) == 1
        assert results.misclassified[0]["actual"] == "clean"
        assert results.misclassified[0]["predicted"] == "violation"


class TestPrintResults:
    def test_passes_high_precision_adequate_recall(self) -> None:
        # precision=100%, recall=82.6%
        r = EvalResults(rule="test-rule", tp=19, fp=0, tn=10, fn=4)
        assert print_results(r) is True

    def test_fails_low_precision(self) -> None:
        # precision=80%, recall=80%
        r = EvalResults(rule="test-rule", tp=8, fp=2, tn=8, fn=2)
        assert print_results(r) is False

    def test_fails_low_recall(self) -> None:
        # precision=100%, recall=70%
        r = EvalResults(rule="test-rule", tp=7, fp=0, tn=10, fn=3)
        assert print_results(r) is False

    def test_passes_at_exact_thresholds(self) -> None:
        # precision=95% (20/21), recall=80% (20/25)
        r = EvalResults(rule="test-rule", tp=20, fp=1, tn=10, fn=5)
        assert print_results(r) is True

    def test_fails_both_below(self) -> None:
        # precision=75%, recall=60%
        r = EvalResults(rule="test-rule", tp=6, fp=2, tn=8, fn=4)
        assert print_results(r) is False
