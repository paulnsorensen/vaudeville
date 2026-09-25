"""Tests for vaudeville/eval.py maths and vaudeville/eval_report.py reporting."""

from __future__ import annotations

from vaudeville.eval import EvalResults
from vaudeville.eval_report import print_results, run_evaluations
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


class TestRunEvaluations:
    def test_skips_rule_with_no_definition(self) -> None:
        passed, all_results, case_results = run_evaluations({}, {"missing-rule": []}, UserConfig())
        assert passed is True
        assert all_results == {}
        assert case_results == []

    def test_evaluates_and_aggregates(self) -> None:
        rule = _rule()
        cases = [DecideTestCase(text="t", outcome="clean")]

        passed, all_results, case_results = run_evaluations(
            {rule.name: rule}, {rule.name: cases}, UserConfig()
        )

        assert rule.name in all_results
        assert all_results[rule.name].fn == 0
        assert len(case_results) == 1
        assert passed is False


class TestUpdateResultsPositiveOutcomes:
    """The positive class is the `on:`-blocking outcome, not outcomes[0]."""

    def test_non_positive_mismatch_is_misclassified_not_tn(self) -> None:
        from vaudeville.eval import EvalResults, _update_results

        rule_data = dict(_RULE)
        rule_data["outcomes"] = ["violation", "ticket-instead", "clean"]
        rule_data["on"] = {"violation": "block"}
        rule = parse_rule(rule_data)
        assert isinstance(rule, DecideRule)
        results = EvalResults(rule=rule.name)

        _update_results(results, rule, "ticket-instead", "clean", "text")

        assert results.tn == 0
        assert results.tp == 0
        assert results.fp == 0
        assert results.fn == 0
        assert results.misclassified == [
            {"text": "text", "actual": "ticket-instead", "predicted": "clean"}
        ]

    def test_reversed_outcome_order_scores_violation_as_positive(self) -> None:
        from vaudeville.eval import EvalResults, _update_results

        rule_data = dict(_RULE)
        rule_data["outcomes"] = ["clean", "violation"]
        rule_data["on"] = {"violation": "block"}
        rule = parse_rule(rule_data)
        assert isinstance(rule, DecideRule)
        results = EvalResults(rule=rule.name)

        _update_results(results, rule, "violation", "violation", "text")

        assert results.tp == 1
        assert results.tn == 0
        assert results.fp == 0
        assert results.fn == 0
