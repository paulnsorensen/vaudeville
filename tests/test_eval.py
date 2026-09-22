"""Tests for vaudeville/eval.py maths and vaudeville/eval_report.py reporting."""

from __future__ import annotations

import argparse

from vaudeville.eval import EvalResults
from vaudeville.eval_report import cross_validate_rule, print_results, run_evaluations
from vaudeville.rules import DecideRule, DecideTestCase, parse_rule
from vaudeville.server.user_config import UserConfig

_RULE = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "model": "anthropic:claude-haiku-4-5",
    "prompt": "Classify the transcript.",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
}


def _rule() -> DecideRule:
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    return rule


class TestEvalResultsMaths:
    def test_empty_results_are_zero(self) -> None:
        results = EvalResults(rule="r")
        assert results.total == 0
        assert results.accuracy == 0.0
        assert results.precision == 0.0
        assert results.recall == 0.0
        assert results.f1 == 0.0

    def test_accuracy_precision_recall_f1(self) -> None:
        results = EvalResults(rule="r", tp=3, fp=1, tn=4, fn=2)
        assert results.total == 10
        assert results.accuracy == 0.7
        assert results.precision == 0.75
        assert results.recall == 0.6
        expected_f1 = 2 * 0.75 * 0.6 / (0.75 + 0.6)
        assert results.f1 == expected_f1

    def test_precision_zero_when_no_predicted_positives(self) -> None:
        results = EvalResults(rule="r", tn=1, fn=1)
        assert results.precision == 0.0
        assert results.f1 == 0.0


class TestPrintResults:
    def test_pass_when_precision_and_recall_met(self, capsys: object) -> None:
        results = EvalResults(rule="r", tp=8, fp=0, tn=2, fn=2)
        passed = print_results(results)
        assert passed is True
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "[PASS]" in out

    def test_fail_when_below_threshold(self, capsys: object) -> None:
        results = EvalResults(rule="r", tp=1, fp=1, tn=1, fn=5)
        passed = print_results(results)
        assert passed is False
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "[FAIL]" in out
        assert "BELOW THRESHOLD" in out

    def test_prints_misclassifications(self, capsys: object) -> None:
        results = EvalResults(rule="r", tp=1, fp=1, tn=1, fn=0)
        results.misclassified.append(
            {"text": "bad text", "actual": "clean", "predicted": "violation"}
        )
        print_results(results)
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "Misclassifications:" in out
        assert "bad text" in out


class TestCrossValidateRule:
    def test_raises_for_unknown_rule(self) -> None:
        try:
            cross_validate_rule("nonexistent", [], {}, UserConfig())
        except ValueError as exc:
            assert "nonexistent" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_produces_correct_totals(self, capsys: object) -> None:
        # No config.providers entry for the rule's model: every fold fails
        # open (predicted is None) without making a network call.
        rule = _rule()
        cases = [
            DecideTestCase(text="violation text", outcome="violation"),
            DecideTestCase(text="clean text", outcome="clean"),
        ]

        aggregate = cross_validate_rule(
            rule.name, cases, {rule.name: rule}, UserConfig()
        )

        assert aggregate.total == 2
        assert aggregate.tp == 0
        assert aggregate.fn == 1  # violation case predicted None
        assert aggregate.tn == 1  # clean case predicted None
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "Fold 1/2" in out
        assert "Fold 2/2" in out


class TestRunEvaluations:
    def test_skips_rule_with_no_definition(self) -> None:
        args = argparse.Namespace(cross_validate=False)
        passed, all_results, case_results = run_evaluations(
            args, {}, {"missing-rule": []}, UserConfig()
        )
        assert passed is True
        assert all_results == {}
        assert case_results == []

    def test_evaluates_and_aggregates(self) -> None:
        rule = _rule()
        cases = [DecideTestCase(text="t", outcome="clean")]
        args = argparse.Namespace(cross_validate=False)

        passed, all_results, case_results = run_evaluations(
            args, {rule.name: rule}, {rule.name: cases}, UserConfig()
        )

        assert rule.name in all_results
        assert all_results[rule.name].fn == 0
        assert len(case_results) == 1
        assert passed is False
