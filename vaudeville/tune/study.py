"""Study configuration, verdict, and Optuna study creation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import optuna

from .sampler import LLMSampler

logger = logging.getLogger(__name__)


@dataclass
class StudyConfig:
    rule_name: str
    p_min: float = 0.95
    r_min: float = 0.80
    budget: int = 15
    study_dir: str = ""
    consecutive_target: int = 2
    author: bool = False

    def resolve_study_dir(self) -> str:
        if self.study_dir:
            return self.study_dir
        return os.path.join(os.path.expanduser("~"), ".vaudeville", "tunes")


@dataclass
class TuneVerdict:
    passed: bool
    p_tune: float
    r_tune: float
    p_held: float
    r_held: float
    trials_run: int
    pool_size: int
    best_ids: list[str]
    study_uri: str
    diff_path: str


def _study_db_path(config: StudyConfig) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    d = config.resolve_study_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{config.rule_name}-{ts}.db")


def _constraints_func(trial: optuna.trial.FrozenTrial) -> list[float]:
    """Return constraint violations for NSGA-II feasibility sorting."""
    violated = trial.user_attrs.get("constraint_violated", False)
    return [1.0 if violated else 0.0]


def _make_default_sampler() -> optuna.samplers.BaseSampler:
    """Build LLMSampler with Anthropic client, or NSGA-II if unavailable."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        return LLMSampler(anthropic_client=client)
    except Exception:
        logger.debug("Anthropic client unavailable, using NSGA-II sampler")
        return optuna.samplers.NSGAIISampler(
            constraints_func=_constraints_func,
        )


def create_study(
    config: StudyConfig,
    sampler: optuna.samplers.BaseSampler | None = None,
) -> tuple[optuna.Study, str]:
    """Create a multi-objective Optuna study persisted to sqlite."""
    db_path = _study_db_path(config)
    storage = f"sqlite:///{db_path}"
    if sampler is None:
        sampler = _make_default_sampler()
    study = optuna.create_study(
        study_name=config.rule_name,
        storage=storage,
        sampler=sampler,
        directions=["maximize", "maximize"],
    )
    return study, db_path
