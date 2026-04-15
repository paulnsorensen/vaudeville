"""Tests for vaudeville.tune.sampler — LLMSampler with TPE fallback."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

optuna = pytest.importorskip("optuna")
from optuna.distributions import BaseDistribution, CategoricalDistribution  # noqa: E402

from vaudeville.tune.sampler import (  # noqa: E402
    COLD_START_TRIALS,
    LLMSampler,
    _build_llm_prompt,
    _format_trial_history,
    _parse_llm_response,
    _recent_completed,
)


@pytest.fixture
def search_space() -> dict[str, BaseDistribution]:
    return {
        "ex1": CategoricalDistribution([0, 1]),
        "ex2": CategoricalDistribution([0, 1]),
    }


@pytest.fixture
def study() -> optuna.Study:
    return optuna.create_study(
        directions=["maximize", "maximize"],
        sampler=optuna.samplers.RandomSampler(),
    )


class TestRecentCompleted:
    def test_empty_study(self, study: optuna.Study) -> None:
        assert _recent_completed(study) == []

    def test_filters_non_complete(self, study: optuna.Study) -> None:
        trial = study.ask()
        study.tell(trial, state=optuna.trial.TrialState.PRUNED)
        assert _recent_completed(study) == []

    def test_returns_completed(self, study: optuna.Study) -> None:
        trial = study.ask()
        study.tell(trial, values=[0.5, 0.5])
        assert len(_recent_completed(study)) == 1


class TestFormatTrialHistory:
    def test_empty_list(self) -> None:
        result = _format_trial_history([])
        assert "(none yet)" in result

    def test_formats_trial(self, study: optuna.Study) -> None:
        trial = study.ask()
        trial.suggest_categorical("ex1", [0, 1])
        study.tell(trial, values=[0.9, 0.8])
        trials = _recent_completed(study)
        result = _format_trial_history(trials)
        assert "trial 0" in result
        assert "0.9000" in result


class TestParseLlmResponse:
    def test_valid_response(
        self,
        search_space: dict[str, BaseDistribution],
    ) -> None:
        text = json.dumps(
            {
                "config": {"ex1": 1, "ex2": 0},
                "hypothesis": "Enable ex1 for better recall",
            }
        )
        config, hyp = _parse_llm_response(text, search_space)
        assert config == {"ex1": 1, "ex2": 0}
        assert "recall" in hyp

    def test_invalid_value_raises(
        self,
        search_space: dict[str, BaseDistribution],
    ) -> None:
        text = json.dumps({"config": {"ex1": 2, "ex2": 0}})
        with pytest.raises(ValueError, match="Invalid value"):
            _parse_llm_response(text, search_space)

    def test_invalid_json_raises(
        self,
        search_space: dict[str, BaseDistribution],
    ) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response("not json", search_space)

    def test_missing_key_raises(
        self,
        search_space: dict[str, BaseDistribution],
    ) -> None:
        text = json.dumps({"config": {"ex1": 1}})
        with pytest.raises((ValueError, KeyError)):
            _parse_llm_response(text, search_space)


class TestBuildLlmPrompt:
    def test_contains_params(
        self,
        study: optuna.Study,
        search_space: dict[str, BaseDistribution],
    ) -> None:
        prompt = _build_llm_prompt(study, search_space)
        assert "ex1" in prompt
        assert "ex2" in prompt
        assert "JSON" in prompt


class TestLLMSampler:
    def test_cold_start_uses_random(
        self,
        study: optuna.Study,
        search_space: dict[str, BaseDistribution],
    ) -> None:
        sampler = LLMSampler(anthropic_client=None)
        trial = study.ask()
        result = sampler.sample_relative(study, trial, search_space)  # type: ignore[arg-type]
        assert trial.user_attrs.get("proposal_source") == "random_seed"
        assert isinstance(result, dict)

    def test_no_client_falls_to_tpe(self, study: optuna.Study) -> None:
        sampler = LLMSampler(anthropic_client=None)
        space: dict[str, BaseDistribution] = {"ex1": CategoricalDistribution([0, 1])}
        for _ in range(COLD_START_TRIALS):
            t = study.ask()
            t.suggest_categorical("ex1", [0, 1])
            study.tell(t, values=[0.5, 0.5])
        trial = study.ask()
        sampler.sample_relative(study, trial, space)  # type: ignore[arg-type]
        assert trial.user_attrs.get("proposal_source") == "tpe_fallback"

    def test_llm_success(self, study: optuna.Study) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "config": {"ex1": 1},
                        "hypothesis": "test hyp",
                    }
                )
            )
        ]
        mock_client.messages.create.return_value = mock_response

        sampler = LLMSampler(anthropic_client=mock_client)
        space: dict[str, BaseDistribution] = {"ex1": CategoricalDistribution([0, 1])}
        for _ in range(COLD_START_TRIALS):
            t = study.ask()
            t.suggest_categorical("ex1", [0, 1])
            study.tell(t, values=[0.5, 0.5])
        trial = study.ask()
        result = sampler.sample_relative(study, trial, space)  # type: ignore[arg-type]
        assert trial.user_attrs.get("proposal_source") == "llm"
        assert trial.user_attrs.get("hypothesis") == "test hyp"
        assert result == {"ex1": 1}

    def test_llm_error_falls_to_tpe(
        self,
        study: optuna.Study,
    ) -> None:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        sampler = LLMSampler(anthropic_client=mock_client)
        space: dict[str, BaseDistribution] = {"ex1": CategoricalDistribution([0, 1])}
        for _ in range(COLD_START_TRIALS):
            t = study.ask()
            t.suggest_categorical("ex1", [0, 1])
            study.tell(t, values=[0.5, 0.5])
        trial = study.ask()
        result = sampler.sample_relative(study, trial, space)  # type: ignore[arg-type]
        assert trial.user_attrs.get("proposal_source") == "tpe_fallback"
        assert isinstance(result, dict)

    def test_empty_search_space(self, study: optuna.Study) -> None:
        sampler = LLMSampler()
        trial = study.ask()
        result = sampler.sample_relative(study, trial, {})  # type: ignore[arg-type]
        assert result == {}

    def test_sample_independent_cold_start(
        self,
        study: optuna.Study,
    ) -> None:
        sampler = LLMSampler()
        trial = study.ask()
        dist = CategoricalDistribution([0, 1])
        val = sampler.sample_independent(study, trial, "ex1", dist)  # type: ignore[arg-type]
        assert val in (0, 1)

    def test_sample_independent_post_cold(
        self,
        study: optuna.Study,
    ) -> None:
        sampler = LLMSampler()
        for _ in range(COLD_START_TRIALS):
            t = study.ask()
            t.suggest_categorical("ex1", [0, 1])
            study.tell(t, values=[0.5, 0.5])
        trial = study.ask()
        dist = CategoricalDistribution([0, 1])
        val = sampler.sample_independent(study, trial, "ex1", dist)  # type: ignore[arg-type]
        assert val in (0, 1)

    def test_after_trial(self, study: optuna.Study) -> None:
        sampler = LLMSampler()
        trial = study.ask()
        trial.suggest_categorical("ex1", [0, 1])
        study.tell(trial, values=[0.5, 0.5])
        completed = _recent_completed(study)
        sampler.after_trial(
            study,
            completed[0],
            optuna.trial.TrialState.COMPLETE,
            [0.5, 0.5],
        )

    def test_infer_relative_search_space(
        self,
        study: optuna.Study,
    ) -> None:
        sampler = LLMSampler()
        t = study.ask()
        t.suggest_categorical("ex1", [0, 1])
        study.tell(t, values=[0.5, 0.5])
        trial = study.ask()
        space = sampler.infer_relative_search_space(study, trial)  # type: ignore[arg-type]
        assert isinstance(space, dict)
        assert "ex1" in space
