"""Tests for vaudeville.tune.harness — Optuna study orchestration."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import optuna
import pytest

from vaudeville.core.protocol import ClassifyResult
from vaudeville.core.rules import Example, Rule
from vaudeville.eval import CaseResult, EvalCase
from vaudeville.tune.harness import (
    PROMPT_BUDGET,
    StudyConfig,
    _check_prompt_budget,
    _compute_metrics,
    _eval_subset,
    _make_trial_rule,
    _pool_ids,
    _study_db_path,
    best_trial_ids,
    create_study,
    run_trial,
)


@pytest.fixture
def simple_rule() -> Rule:
    return Rule(
        name="test-rule",
        event="Stop",
        prompt="Classify: {{ examples }}\n{text}",
        context=[],
        action="block",
        message="{reason}",
        examples=[
            Example(id="ex1", input="bad", label="violation", reason="bad"),
            Example(id="ex2", input="good", label="clean", reason="good"),
        ],
        candidates=[
            Example(id="c1", input="ugly", label="violation", reason="ugly"),
        ],
    )


@pytest.fixture
def mock_backend() -> MagicMock:
    backend = MagicMock()
    backend.classify.return_value = "VERDICT: violation\nREASON: test"
    backend.classify_with_logprobs.return_value = ClassifyResult(
        text="VERDICT: violation\nREASON: test",
        logprobs={},
    )
    return backend


@pytest.fixture
def eval_cases() -> list[EvalCase]:
    return [
        EvalCase(text="this is bad", label="violation"),
        EvalCase(text="this is fine", label="clean"),
    ]


class TestPoolIds:
    def test_returns_all_ids(self, simple_rule: Rule) -> None:
        ids = _pool_ids(simple_rule)
        assert ids == ["ex1", "ex2", "c1"]

    def test_empty_rule(self) -> None:
        rule = Rule(
            name="empty",
            event="Stop",
            prompt="test",
            context=[],
            action="block",
            message="",
        )
        assert _pool_ids(rule) == []


class TestComputeMetrics:
    def test_perfect_scores(self) -> None:
        results = [
            CaseResult("r", 0, "x", "violation", "violation", 0.9),
            CaseResult("r", 1, "y", "clean", "clean", 0.9),
        ]
        p, r = _compute_metrics(results)
        assert p == 1.0
        assert r == 1.0

    def test_all_fp(self) -> None:
        results = [
            CaseResult("r", 0, "x", "clean", "violation", 0.9),
        ]
        p, _ = _compute_metrics(results)
        assert p == 0.0

    def test_all_fn(self) -> None:
        results = [
            CaseResult("r", 0, "x", "violation", "clean", 0.9),
        ]
        _, r = _compute_metrics(results)
        assert r == 0.0

    def test_empty_results(self) -> None:
        p, r = _compute_metrics([])
        assert p == 1.0
        assert r == 1.0


class TestCheckPromptBudget:
    def test_within_budget(self, simple_rule: Rule) -> None:
        assert _check_prompt_budget(simple_rule, ["ex1"])

    def test_over_budget(self) -> None:
        rule = Rule(
            name="big",
            event="Stop",
            prompt="x" * (PROMPT_BUDGET + 1),
            context=[],
            action="block",
            message="",
        )
        assert not _check_prompt_budget(rule, [])


class TestMakeTrialRule:
    def test_renders_prompt(self, simple_rule: Rule) -> None:
        trial_rule = _make_trial_rule(simple_rule, ["ex1"])
        assert "{{ examples }}" not in trial_rule.prompt
        assert "bad" in trial_rule.prompt
        assert trial_rule.examples == []
        assert trial_rule.candidates == []

    def test_preserves_metadata(self, simple_rule: Rule) -> None:
        trial_rule = _make_trial_rule(simple_rule, ["ex1"])
        assert trial_rule.name == "test-rule"
        assert trial_rule.event == "Stop"


class TestStudyConfig:
    def test_defaults(self) -> None:
        cfg = StudyConfig(rule_name="test")
        assert cfg.p_min == 0.95
        assert cfg.r_min == 0.80
        assert cfg.budget == 15

    def test_resolve_study_dir_default(self) -> None:
        cfg = StudyConfig(rule_name="test")
        d = cfg.resolve_study_dir()
        assert ".vaudeville" in d
        assert "tunes" in d

    def test_resolve_study_dir_custom(self) -> None:
        cfg = StudyConfig(rule_name="test", study_dir="/tmp/custom")
        assert cfg.resolve_study_dir() == "/tmp/custom"


class TestStudyDbPath:
    def test_creates_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="my-rule", study_dir=tmpdir)
            path = _study_db_path(cfg)
            assert path.startswith(tmpdir)
            assert "my-rule" in path
            assert path.endswith(".db")


class TestCreateStudy:
    def test_creates_study(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="test", study_dir=tmpdir)
            study, db_path = create_study(cfg)
            assert study.study_name == "test"
            assert os.path.exists(db_path)
            assert study.directions == [
                optuna.study.StudyDirection.MAXIMIZE,
                optuna.study.StudyDirection.MAXIMIZE,
            ]

    def test_custom_sampler(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="test", study_dir=tmpdir)
            sampler = optuna.samplers.RandomSampler()
            study, _ = create_study(cfg, sampler=sampler)
            assert study.sampler is sampler


class TestEvalSubset:
    def test_evaluates_cases(
        self,
        simple_rule: Rule,
        mock_backend: MagicMock,
        eval_cases: list[EvalCase],
    ) -> None:
        p, r, results = _eval_subset(
            simple_rule,
            ["ex1", "ex2"],
            eval_cases,
            mock_backend,
        )
        assert isinstance(p, float)
        assert isinstance(r, float)
        assert len(results) == 2


class _AllOneSampler(optuna.samplers.BaseSampler):
    """Sampler that always returns 1 for categorical params."""

    def infer_relative_search_space(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
    ) -> dict[str, optuna.distributions.BaseDistribution]:
        return {}

    def sample_relative(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
        search_space: dict[str, optuna.distributions.BaseDistribution],
    ) -> dict[str, int]:
        return {}

    def sample_independent(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
        param_name: str,
        param_distribution: optuna.distributions.BaseDistribution,
    ) -> int:
        return 1


class TestRunTrial:
    def test_runs_trial(
        self,
        simple_rule: Rule,
        mock_backend: MagicMock,
        eval_cases: list[EvalCase],
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(
                rule_name="test-rule",
                study_dir=tmpdir,
            )
            study, _ = create_study(cfg, sampler=_AllOneSampler())
            trial = study.ask()
            r_held, p_held = run_trial(
                trial,
                simple_rule,
                eval_cases,
                eval_cases,
                mock_backend,
                cfg,
            )
            assert isinstance(r_held, float)
            assert isinstance(p_held, float)

    def test_prunes_empty_pool(
        self,
        mock_backend: MagicMock,
        eval_cases: list[EvalCase],
    ) -> None:
        rule = Rule(
            name="empty",
            event="Stop",
            prompt="test {text}",
            context=[],
            action="block",
            message="",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="empty", study_dir=tmpdir)
            study, _ = create_study(cfg)
            trial = study.ask()
            with pytest.raises(optuna.TrialPruned, match="No examples"):
                run_trial(
                    trial,
                    rule,
                    eval_cases,
                    eval_cases,
                    mock_backend,
                    cfg,
                )

    def test_prunes_empty_selection(
        self,
        simple_rule: Rule,
        mock_backend: MagicMock,
        eval_cases: list[EvalCase],
    ) -> None:
        """All toggles set to 0 → prune."""

        class _AllZeroSampler(optuna.samplers.BaseSampler):
            def infer_relative_search_space(
                self,
                study: optuna.Study,
                trial: optuna.trial.FrozenTrial,
            ) -> dict[str, optuna.distributions.BaseDistribution]:
                return {}

            def sample_relative(
                self,
                study: optuna.Study,
                trial: optuna.trial.FrozenTrial,
                search_space: dict[str, optuna.distributions.BaseDistribution],
            ) -> dict[str, int]:
                return {}

            def sample_independent(
                self,
                study: optuna.Study,
                trial: optuna.trial.FrozenTrial,
                param_name: str,
                param_distribution: optuna.distributions.BaseDistribution,
            ) -> int:
                return 0

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="test-rule", study_dir=tmpdir)
            study, _ = create_study(cfg, sampler=_AllZeroSampler())
            trial = study.ask()
            with pytest.raises(optuna.TrialPruned, match="Empty selection"):
                run_trial(
                    trial,
                    simple_rule,
                    eval_cases,
                    eval_cases,
                    mock_backend,
                    cfg,
                )

    def test_prunes_over_budget(
        self,
        mock_backend: MagicMock,
        eval_cases: list[EvalCase],
    ) -> None:
        big_rule = Rule(
            name="big",
            event="Stop",
            prompt="x" * (PROMPT_BUDGET - 10) + "{{ examples }}\n{text}",
            context=[],
            action="block",
            message="",
            examples=[
                Example(id="ex1", input="a" * 100, label="violation", reason="r"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="big", study_dir=tmpdir)
            study, _ = create_study(cfg, sampler=_AllOneSampler())
            trial = study.ask()
            with pytest.raises(optuna.TrialPruned, match="Exceeds prompt budget"):
                run_trial(
                    trial,
                    big_rule,
                    eval_cases,
                    eval_cases,
                    mock_backend,
                    cfg,
                )


class TestBestTrialIds:
    def test_returns_none_when_no_trials(self) -> None:
        study = optuna.create_study(directions=["maximize"])
        assert best_trial_ids(study) is None

    def test_returns_ids_from_best(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = StudyConfig(rule_name="test", study_dir=tmpdir)
            study, _ = create_study(cfg)
            trial = study.ask()
            trial.set_user_attr("example_ids", ["ex1", "ex2"])
            study.tell(trial, values=[0.9, 0.9])
            ids = best_trial_ids(study)
            assert ids == ["ex1", "ex2"]
