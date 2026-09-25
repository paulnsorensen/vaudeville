"""E2E tests for example rules — verify they load, parse, and eval cleanly."""

from __future__ import annotations

import os

import pytest

from vaudeville.eval import EvalResults, classify_case, load_test_cases
from vaudeville.rules import DecideRule, DecideTestCase, load_rules
from vaudeville.server.user_config import UserConfig

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_RULES_DIR = os.path.join(PROJECT_ROOT, "examples", "rules")

MIN_CASES_PER_RULE = 10
MIN_TEXT_LENGTH = 50  # runner.py skips shorter inputs


@pytest.fixture
def example_rules() -> dict[str, DecideRule]:
    rules = load_rules(EXAMPLES_RULES_DIR)
    assert rules, f"No rules found in {EXAMPLES_RULES_DIR}"
    decide_rules = {name: rule for name, rule in rules.items() if isinstance(rule, DecideRule)}
    assert decide_rules, f"No decide rules found in {EXAMPLES_RULES_DIR}"
    return decide_rules


@pytest.fixture
def example_test_suites(
    example_rules: dict[str, DecideRule],
) -> dict[str, list[DecideTestCase]]:
    suites = load_test_cases(example_rules)  # type: ignore[arg-type]
    assert suites, "No test cases found on example rules"
    return suites


class TestExampleRulesLoad:
    """Verify all example rules parse correctly via load_rules."""

    def test_all_rules_have_required_fields(self, example_rules: dict[str, DecideRule]) -> None:
        for name, rule in example_rules.items():
            assert rule.name == name, f"{name}: name mismatch"
            assert rule.prompt, f"{name}: empty prompt"
            assert rule.event, f"{name}: no event"
            assert rule.outcomes, f"{name}: no outcomes"

    def test_on_actions_reference_declared_outcomes(
        self, example_rules: dict[str, DecideRule]
    ) -> None:
        for name, rule in example_rules.items():
            for outcome in rule.on:
                assert outcome in rule.outcomes, (
                    f"{name}: on-action outcome {outcome!r} not in {rule.outcomes}"
                )


class TestExampleTestCases:
    """Verify test case quality — balanced, sufficient, realistic."""

    def test_every_rule_has_test_cases(
        self,
        example_rules: dict[str, DecideRule],
        example_test_suites: dict[str, list[DecideTestCase]],
    ) -> None:
        for name in example_rules:
            assert name in example_test_suites, f"{name}: no test cases found"

    def test_minimum_case_count(
        self, example_test_suites: dict[str, list[DecideTestCase]]
    ) -> None:
        for name, cases in example_test_suites.items():
            assert len(cases) >= MIN_CASES_PER_RULE, (
                f"{name}: {len(cases)} cases < minimum {MIN_CASES_PER_RULE}"
            )

    def test_outcomes_are_balanced(
        self,
        example_rules: dict[str, DecideRule],
        example_test_suites: dict[str, list[DecideTestCase]],
    ) -> None:
        for name, cases in example_test_suites.items():
            positive = example_rules[name].outcomes[0]
            positives = sum(1 for c in cases if c.outcome == positive)
            negatives = len(cases) - positives
            assert positives > 0, f"{name}: no {positive} cases"
            assert negatives > 0, f"{name}: no negative cases"
            ratio = positives / len(cases)
            assert 0.3 <= ratio <= 0.7, (
                f"{name}: imbalanced outcomes ({positives}/{len(cases)} {positive})"
            )

    def test_all_texts_above_min_length(
        self, example_test_suites: dict[str, list[DecideTestCase]]
    ) -> None:
        for name, cases in example_test_suites.items():
            for i, case in enumerate(cases):
                assert len(case.text) >= MIN_TEXT_LENGTH, (
                    f"{name} case {i}: text too short ({len(case.text)} chars) "
                    f"— runner.py skips inputs under {MIN_TEXT_LENGTH} chars"
                )

    def test_outcomes_are_declared_on_the_rule(
        self,
        example_rules: dict[str, DecideRule],
        example_test_suites: dict[str, list[DecideTestCase]],
    ) -> None:
        for name, cases in example_test_suites.items():
            valid = set(example_rules[name].outcomes)
            for case in cases:
                assert case.outcome in valid, (
                    f"{name}: invalid outcome {case.outcome!r} not in {valid}"
                )


class TestExampleEvalPipeline:
    """Run the full eval pipeline fail-open (no model configured) to verify no errors."""

    def test_eval_runs_for_all_example_rules(
        self,
        example_rules: dict[str, DecideRule],
        example_test_suites: dict[str, list[DecideTestCase]],
    ) -> None:
        config = UserConfig()
        for name, rule in example_rules.items():
            cases = example_test_suites.get(name, [])
            if not cases:
                continue
            results = EvalResults(rule=name)
            case_results = [
                classify_case(case, rule, config, results, case_id=i)
                for i, case in enumerate(cases)
            ]
            assert results.total == len(cases), (
                f"{name}: expected {len(cases)} results, got {results.total}"
            )
            assert len(case_results) == len(cases)
            assert all(cr.predicted is None for cr in case_results), (
                f"{name}: fail-open should predict None for every case"
            )
