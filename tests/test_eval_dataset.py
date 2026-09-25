"""Eval Dataset tests (AC-1): build_dataset/run_dataset on pydantic_evals.

Parity: TP/FP/TN/FN/precision/recall/F1 from `evaluate_rule` running through
`Dataset.evaluate_sync` must equal the prior hand-rolled harness math.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Dataset

from vaudeville.eval import build_dataset, evaluate_rule, run_dataset
from vaudeville.rules import DecideRule, DecideTestCase, RewriteRule, parse_rule
from vaudeville.server.agents.decide import DecideResult
from vaudeville.server.user_config import ProviderConfig, UserConfig

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
    return UserConfig(providers={"anthropic": ProviderConfig(key_env="ANTHROPIC_API_KEY")})


def _function_model(responses: dict[str, str]) -> FunctionModel:
    """Build a FunctionModel that replies by matching case text in the prompt.

    Keys on the case text inside the delimited user prompt, not call order,
    so responses stay correct regardless of scheduling.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompt_parts = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert prompt_parts, f"no UserPromptPart in messages: {messages!r}"
        prompt = prompt_parts[-1].content
        assert isinstance(prompt, str)
        for text, output in responses.items():
            if text in prompt:
                return ModelResponse(parts=[TextPart(output)])
        raise AssertionError(f"no response configured for prompt: {prompt!r}")

    return FunctionModel(respond)


def test_dataset_built_from_inline_cases() -> None:
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    rule = rule.model_copy(
        update={
            "test_cases": [
                DecideTestCase(text="one", outcome="violation"),
                DecideTestCase(text="two", outcome="clean"),
            ]
        }
    )

    dataset = build_dataset(rule)

    assert isinstance(dataset, Dataset)
    assert len(dataset.cases) == 2
    assert [c.inputs for c in dataset.cases] == ["one", "two"]
    expected = [c.expected_output for c in dataset.cases]
    assert all(e is not None for e in expected)
    assert [e.outcome for e in expected if e is not None] == ["violation", "clean"]


def test_parity_with_prior_harness_confusion_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    cases = [
        DecideTestCase(text="violation text one", outcome="violation"),
        DecideTestCase(text="violation text two", outcome="violation"),
        DecideTestCase(text="clean text one", outcome="clean"),
        DecideTestCase(text="clean text two", outcome="clean"),
    ]
    model = _function_model(
        {
            "violation text one": '{"outcome": "violation"}',  # tp
            "violation text two": '{"outcome": "clean"}',  # fn
            "clean text one": '{"outcome": "violation"}',  # fp
            "clean text two": '{"outcome": "clean"}',  # tn
        }
    )
    rules: dict[str, DecideRule | RewriteRule] = {rule.name: rule}

    results, case_results = evaluate_rule(rule.name, cases, rules, _config(), model_override=model)

    assert (results.tp, results.fp, results.tn, results.fn) == (1, 1, 1, 1)
    assert results.precision == pytest.approx(0.5)
    assert results.recall == pytest.approx(0.5)
    assert results.f1 == pytest.approx(0.5)
    assert len(case_results) == 4


def test_parity_with_prior_harness_fail_open_none_counts_as_tn() -> None:
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    cases = [DecideTestCase(text="should I commit?", outcome="clean")]
    rules: dict[str, DecideRule | RewriteRule] = {rule.name: rule}

    # No model_override and no provider key configured: decide() fails open (outcome None).
    results, case_results = evaluate_rule(rule.name, cases, rules, UserConfig())

    assert case_results[0].predicted is None
    assert results.tn == 1
    assert results.total == 1


def test_parity_with_prior_harness_nonpositive_mismatch_not_tn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rule = parse_rule(
        {
            **_RULE,
            "outcomes": ["violation", "ticket-instead", "clean"],
            "on": {"violation": "block", "ticket-instead": "allow"},
        }
    )
    assert isinstance(rule, DecideRule)
    cases = [DecideTestCase(text="text", outcome="clean")]
    model = _function_model({"text": '{"outcome": "ticket-instead"}'})
    rules: dict[str, DecideRule | RewriteRule] = {rule.name: rule}

    results, case_results = evaluate_rule(rule.name, cases, rules, _config(), model_override=model)

    assert (results.tp, results.fp, results.tn, results.fn) == (0, 0, 0, 0)
    assert results.misclassified == [
        {"text": "text", "actual": "clean", "predicted": "ticket-instead"}
    ]
    assert case_results[0].predicted == "ticket-instead"


def test_task_output_carries_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_decide(
        rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
    ) -> DecideResult:
        del rule, config, text, model_override
        return DecideResult(outcome="violation", confidence=0.7)

    monkeypatch.setattr("vaudeville.eval.decide", fake_decide)
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    rule = rule.model_copy(
        update={"test_cases": [DecideTestCase(text="one", outcome="violation")]}
    )
    dataset = build_dataset(rule)

    report = run_dataset(dataset, rule, _config())

    assert report.cases[0].output.confidence == 0.7
    assert report.cases[0].metrics == {"confidence": 0.7}


def test_task_output_none_confidence_stays_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_decide(
        rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
    ) -> DecideResult:
        del rule, config, text, model_override
        return DecideResult(outcome="clean", confidence=None)

    monkeypatch.setattr("vaudeville.eval.decide", fake_decide)
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    rule = rule.model_copy(update={"test_cases": [DecideTestCase(text="one", outcome="clean")]})
    dataset = build_dataset(rule)

    report = run_dataset(dataset, rule, _config())

    assert report.cases[0].output.confidence is None
    assert "confidence" not in report.cases[0].metrics


def test_precision_recall_uses_outcome_match_not_always_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "violation text one": DecideResult(outcome="violation", confidence=0.9),  # match
        "violation text two": DecideResult(outcome="clean", confidence=0.8),  # mismatch
        "clean text one": DecideResult(outcome="violation", confidence=0.3),  # mismatch
        "clean text two": DecideResult(outcome="clean", confidence=0.6),  # match
    }

    def fake_decide(
        rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
    ) -> DecideResult:
        del rule, config, model_override
        return responses[text]

    monkeypatch.setattr("vaudeville.eval.decide", fake_decide)
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    rule = rule.model_copy(
        update={
            "test_cases": [
                DecideTestCase(text="violation text one", outcome="violation"),
                DecideTestCase(text="violation text two", outcome="violation"),
                DecideTestCase(text="clean text one", outcome="clean"),
                DecideTestCase(text="clean text two", outcome="clean"),
            ]
        }
    )
    dataset = build_dataset(rule)

    report = run_dataset(dataset, rule, _config())

    pr_auc = next(a for a in report.analyses if a.type == "scalar" and "AUC" in a.title)
    assert pr_auc.value < 1.0


def test_run_dataset_raises_on_task_failure_with_original_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rule = parse_rule(_RULE)
    assert isinstance(rule, DecideRule)
    rule = rule.model_copy(
        update={"test_cases": [DecideTestCase(text="one", outcome="violation")]}
    )
    dataset = build_dataset(rule)

    def fake_decide(
        rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
    ) -> DecideResult:
        del rule, config, text, model_override
        raise ConnectionError("upstream 503")

    monkeypatch.setattr("vaudeville.eval.decide", fake_decide)

    with pytest.raises(RuntimeError, match="upstream 503") as exc_info:
        run_dataset(dataset, rule, _config())
    assert "ConnectionError" in str(exc_info.value)
