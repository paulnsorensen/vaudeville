"""Tests for rule loading and eval harness mechanics."""

from __future__ import annotations

import pytest

from vaudeville.core.rules import load_rules
from vaudeville.eval import (
    load_test_cases,
    EvalCase,
    EvalResults,
    evaluate_rule,
    print_results,
)
from conftest import MockBackend


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
        for rule_name, cases in suites.items():
            assert len(cases) >= 10, f"{rule_name}: only {len(cases)} test cases"

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
    def test_perfect_accuracy(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(verdict="violation")
        cases = [EvalCase(text="test", label="violation")] * 5
        results = evaluate_rule("violation-detector", cases, rules, backend)
        assert results.accuracy == 1.0
        assert results.tp == 5

    def test_all_false_positives(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(verdict="violation")
        cases = [EvalCase(text="test", label="clean")] * 5
        results = evaluate_rule("violation-detector", cases, rules, backend)
        assert results.fp == 5
        assert results.accuracy == 0.0

    def test_unknown_rule_raises(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend()
        with pytest.raises(ValueError, match="not found"):
            evaluate_rule("nonexistent", [], rules, backend)

    def test_misclassifications_recorded(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        backend = MockBackend(verdict="violation")
        cases = [EvalCase(text="clean text", label="clean")]
        results = evaluate_rule("violation-detector", cases, rules, backend)
        assert results.misclassified is not None
        assert len(results.misclassified) == 1
        assert results.misclassified[0]["actual"] == "clean"
        assert results.misclassified[0]["predicted"] == "violation"


class TestPrintResults:
    def test_passes_at_90_percent(self) -> None:
        r = EvalResults(rule="test-rule", tp=9, tn=9, fp=1, fn=1)
        passed = print_results(r)
        assert passed is True

    def test_fails_below_90_percent(self) -> None:
        r = EvalResults(rule="test-rule", tp=7, tn=7, fp=3, fn=3)
        passed = print_results(r)
        assert passed is False
