"""Eval tests against the new typed rule format (AC-20).

`just eval`/`python -m vaudeville.eval` loads the bundled example rules
through `vaudeville.rules`, decides each inline test case through
`vaudeville.server.agents.decide`, and reports tp/fp/tn/fn per rule.
"""

from __future__ import annotations

import os

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.eval import EvalResults, classify_case, evaluate_rule, load_test_cases
from vaudeville.rules import DecideRule, RewriteRule, load_rules, parse_rule
from vaudeville.server.user_config import ProviderConfig, UserConfig

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_RULES_DIR = os.path.join(PROJECT_ROOT, "examples", "rules")

_RULE = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "model": "anthropic:claude-haiku-4-5",
    "prompt": "Classify the transcript.",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
}


def _config() -> UserConfig:
    return UserConfig(
        providers={"anthropic": ProviderConfig(key_env="ANTHROPIC_API_KEY")}
    )


def _function_model(output: str) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart(output)])

    return FunctionModel(respond)


class TestBundledRulesLoad:
    def test_bundled_rules_load_as_decide_rules(self) -> None:
        rules = load_rules(EXAMPLES_RULES_DIR)
        assert rules, f"No rules found in {EXAMPLES_RULES_DIR}"
        for name, rule in rules.items():
            assert isinstance(rule, DecideRule), f"{name}: not a DecideRule"
            assert rule.outcomes, f"{name}: no outcomes"
            assert rule.test_cases, f"{name}: no test cases"

    def test_bundled_rules_load_case_texts_and_outcomes(self) -> None:
        rules = load_rules(EXAMPLES_RULES_DIR)
        for name, rule in rules.items():
            assert isinstance(rule, DecideRule), f"{name}: not a DecideRule"
            for case in rule.test_cases:
                assert case.text, f"{name}: empty test case text"
                assert case.outcome in rule.outcomes, (
                    f"{name}: test case outcome {case.outcome!r} not in {rule.outcomes}"
                )


class TestNewFormatClassifyCase:
    def test_new_format_classify_case_calls_decide_and_scores_tp(
        self, monkeypatch: object
    ) -> None:
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        rule = parse_rule(_RULE)
        assert isinstance(rule, DecideRule)
        model = _function_model('{"outcome": "violation", "confidence": 0.9}')
        results = EvalResults(rule="git-gate")
        case = rule.test_cases[0] if rule.test_cases else None
        from vaudeville.rules import DecideTestCase

        case = DecideTestCase(text="should I commit?", outcome="violation")

        case_result = classify_case(
            case, rule, _config(), results, model_override=model
        )

        assert case_result.predicted == "violation"
        assert case_result.expected == "violation"
        assert results.tp == 1
        assert results.total == 1
        mp.undo()

    def test_new_format_classify_case_fail_open_counts_as_negative(self) -> None:
        rule = parse_rule(_RULE)
        assert isinstance(rule, DecideRule)
        results = EvalResults(rule="git-gate")
        from vaudeville.rules import DecideTestCase

        case = DecideTestCase(text="should I commit?", outcome="violation")

        # No API key set, no model_override reaching resolve_model: fails open.
        case_result = classify_case(case, rule, UserConfig(), results)

        assert case_result.predicted is None
        assert results.fn == 1
        assert results.tp == 0


class TestConfusionCounts:
    def test_confusion_counts_tp_fp_tn_fn(self, monkeypatch: object) -> None:
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        rule = parse_rule(_RULE)
        assert isinstance(rule, DecideRule)
        from vaudeville.rules import DecideTestCase

        cases = [
            DecideTestCase(text="violation text one", outcome="violation"),
            DecideTestCase(text="violation text two", outcome="violation"),
            DecideTestCase(text="clean text one", outcome="clean"),
            DecideTestCase(text="clean text two", outcome="clean"),
        ]
        outputs = iter(
            [
                '{"outcome": "violation"}',  # tp
                '{"outcome": "clean"}',  # fn
                '{"outcome": "violation"}',  # fp
                '{"outcome": "clean"}',  # tn
            ]
        )

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del messages, info
            return ModelResponse(parts=[TextPart(next(outputs))])

        model = FunctionModel(respond)
        rules: dict[str, DecideRule | RewriteRule] = {rule.name: rule}

        results, case_results = evaluate_rule(
            rule.name, cases, rules, _config(), model_override=model
        )

        assert results.tp == 1
        assert results.fn == 1
        assert results.fp == 1
        assert results.tn == 1
        assert results.total == 4
        assert len(case_results) == 4
        mp.undo()

    def test_confusion_counts_unknown_rule_raises(self) -> None:
        try:
            evaluate_rule("nonexistent", [], {}, UserConfig())
        except ValueError as exc:
            assert "nonexistent" in str(exc)
        else:
            raise AssertionError("expected ValueError")


class TestLoadTestCases:
    def test_new_format_load_test_cases_keyed_by_rule_name(self) -> None:
        rule = parse_rule(_RULE)
        assert isinstance(rule, DecideRule)
        from vaudeville.rules import DecideTestCase

        rule = rule.model_copy(
            update={
                "test_cases": [DecideTestCase(text="t", outcome="violation")],
            }
        )
        suites = load_test_cases({rule.name: rule})

        assert suites == {"git-gate": [DecideTestCase(text="t", outcome="violation")]}

    def test_load_test_cases_omits_rules_without_cases(self) -> None:
        rule = parse_rule(_RULE)
        assert isinstance(rule, DecideRule)

        suites = load_test_cases({rule.name: rule})

        assert suites == {}
