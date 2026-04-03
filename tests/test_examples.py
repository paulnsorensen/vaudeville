"""E2E tests for example rules — verify they load, parse, and eval cleanly."""

from __future__ import annotations

import os

import pytest

from conftest import MockBackend
from vaudeville.core.rules import Rule, load_rules
from vaudeville.eval import EvalCase, evaluate_rule, load_test_cases

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_RULES_DIR = os.path.join(PROJECT_ROOT, "examples", "rules")
EXAMPLES_TESTS_DIR = os.path.join(PROJECT_ROOT, "examples", "tests")

MIN_CASES_PER_RULE = 10
MIN_TEXT_LENGTH = 100  # runner.py skips shorter inputs


@pytest.fixture
def example_rules() -> dict[str, Rule]:
    rules = load_rules(EXAMPLES_RULES_DIR)
    assert rules, f"No rules found in {EXAMPLES_RULES_DIR}"
    return rules


@pytest.fixture
def example_test_suites() -> dict[str, list[EvalCase]]:
    suites = load_test_cases(EXAMPLES_TESTS_DIR)
    assert suites, f"No test suites found in {EXAMPLES_TESTS_DIR}"
    return suites


class TestExampleRulesLoad:
    """Verify all example rules parse correctly via load_rules."""

    def test_all_rules_have_required_fields(
        self, example_rules: dict[str, Rule]
    ) -> None:
        for name, rule in example_rules.items():
            assert rule.name == name, f"{name}: name mismatch"
            assert rule.prompt, f"{name}: empty prompt"
            assert rule.context, f"{name}: no context entries"
            assert rule.event, f"{name}: no event"

    def test_prompts_contain_text_placeholder(
        self, example_rules: dict[str, Rule]
    ) -> None:
        for name, rule in example_rules.items():
            assert "{text}" in rule.prompt, f"{name}: missing {{text}} placeholder"

    def test_prompts_format_without_error(self, example_rules: dict[str, Rule]) -> None:
        for name, rule in example_rules.items():
            formatted = rule.format_prompt("sample input text")
            assert "sample input text" in formatted, f"{name}: text not interpolated"
            assert "{text}" not in formatted, f"{name}: placeholder not replaced"


class TestExampleTestCases:
    """Verify test case quality — balanced, sufficient, realistic."""

    def test_every_rule_has_test_cases(
        self,
        example_rules: dict[str, Rule],
        example_test_suites: dict[str, list[EvalCase]],
    ) -> None:
        for name in example_rules:
            assert name in example_test_suites, f"{name}: no test cases found"

    def test_minimum_case_count(
        self, example_test_suites: dict[str, list[EvalCase]]
    ) -> None:
        for name, cases in example_test_suites.items():
            assert len(cases) >= MIN_CASES_PER_RULE, (
                f"{name}: {len(cases)} cases < minimum {MIN_CASES_PER_RULE}"
            )

    def test_labels_are_balanced(
        self, example_test_suites: dict[str, list[EvalCase]]
    ) -> None:
        for name, cases in example_test_suites.items():
            violations = sum(1 for c in cases if c.label == "violation")
            cleans = sum(1 for c in cases if c.label == "clean")
            assert violations > 0, f"{name}: no violation cases"
            assert cleans > 0, f"{name}: no clean cases"
            ratio = violations / len(cases)
            assert 0.3 <= ratio <= 0.7, (
                f"{name}: imbalanced labels ({violations}v/{cleans}c)"
            )

    def test_all_texts_above_min_length(
        self, example_test_suites: dict[str, list[EvalCase]]
    ) -> None:
        for name, cases in example_test_suites.items():
            for i, case in enumerate(cases):
                assert len(case.text) >= MIN_TEXT_LENGTH, (
                    f"{name} case {i}: text too short ({len(case.text)} chars) "
                    f"— runner.py skips inputs under {MIN_TEXT_LENGTH} chars"
                )

    def test_labels_are_valid(
        self, example_test_suites: dict[str, list[EvalCase]]
    ) -> None:
        valid = {"violation", "clean"}
        for name, cases in example_test_suites.items():
            for case in cases:
                assert case.label in valid, f"{name}: invalid label '{case.label}'"


class TestExampleEvalPipeline:
    """Run the full eval pipeline with MockBackend to verify no errors."""

    def test_eval_runs_for_all_example_rules(
        self,
        example_rules: dict[str, Rule],
        example_test_suites: dict[str, list[EvalCase]],
    ) -> None:
        backend = MockBackend(verdict="violation", reason="mock test")
        for name in example_rules:
            cases = example_test_suites.get(name, [])
            if not cases:
                continue
            results, case_results = evaluate_rule(name, cases, example_rules, backend)
            assert results.total == len(cases), (
                f"{name}: expected {len(cases)} results, got {results.total}"
            )
            assert len(case_results) == len(cases)

    def test_eval_with_clean_backend(
        self,
        example_rules: dict[str, Rule],
        example_test_suites: dict[str, list[EvalCase]],
    ) -> None:
        backend = MockBackend(verdict="clean", reason="mock clean")
        for name in example_rules:
            cases = example_test_suites.get(name, [])
            if not cases:
                continue
            results, _ = evaluate_rule(name, cases, example_rules, backend)
            assert results.total == len(cases)
            assert results.fp == 0, f"{name}: clean backend should have 0 FP"

    def test_prompts_include_case_text(self, example_rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="clean")
        test_text = "This is a specific test string for prompt verification."
        for name, rule in example_rules.items():
            backend.calls.clear()
            evaluate_rule(
                name,
                [EvalCase(text=test_text, label="clean")],
                example_rules,
                backend,
            )
            assert len(backend.calls) == 1
            assert test_text in backend.calls[0], (
                f"{name}: case text not in formatted prompt"
            )
