"""Optuna study orchestration for rule tuning.

Creates and runs a multi-objective study that toggles example IDs
to optimize precision and recall on a held-out set.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import optuna

from ..core.rules import Rule, render_prompt
from ..eval import CaseResult, EvalCase, evaluate_rule
from ..server.inference import InferenceBackend

logger = logging.getLogger(__name__)

PROMPT_BUDGET = 2000


@dataclass
class TrialResult:
    precision_tune: float
    recall_tune: float
    precision_held: float
    recall_held: float
    example_ids: list[str]
    prompt_len: int


@dataclass
class StudyConfig:
    rule_name: str
    p_min: float = 0.95
    r_min: float = 0.80
    budget: int = 15
    study_dir: str = ""

    def resolve_study_dir(self) -> str:
        if self.study_dir:
            return self.study_dir
        return os.path.join(os.path.expanduser("~"), ".vaudeville", "tunes")


def _study_db_path(config: StudyConfig) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    d = config.resolve_study_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{config.rule_name}-{ts}.db")


def _pool_ids(rule: Rule) -> list[str]:
    """All available example + candidate IDs."""
    return [ex.id for ex in rule.examples + rule.candidates]


def _compute_metrics(
    case_results: list[CaseResult],
) -> tuple[float, float]:
    """Compute (precision, recall) from case results."""
    tp = sum(
        1 for c in case_results if c.label == "violation" and c.predicted == "violation"
    )
    fp = sum(
        1 for c in case_results if c.label == "clean" and c.predicted == "violation"
    )
    fn = sum(
        1 for c in case_results if c.label == "violation" and c.predicted == "clean"
    )
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


def _check_prompt_budget(rule: Rule, ids: list[str]) -> bool:
    """Check rendered prompt fits within the budget."""
    rendered = render_prompt(rule, ids)
    return len(rendered) <= PROMPT_BUDGET


def _make_trial_rule(rule: Rule, selected_ids: list[str]) -> Rule:
    """Create a Rule copy with prompt pre-rendered for selected IDs."""
    rendered = render_prompt(rule, selected_ids)
    return Rule(
        name=rule.name,
        event=rule.event,
        prompt=rendered,
        context=rule.context,
        action=rule.action,
        message=rule.message,
        threshold=rule.threshold,
        examples=[],
        candidates=[],
    )


def _eval_subset(
    rule: Rule,
    selected_ids: list[str],
    cases: list[EvalCase],
    backend: InferenceBackend,
) -> tuple[float, float, list[CaseResult]]:
    """Evaluate rule with a specific example selection on cases."""
    trial_rule = _make_trial_rule(rule, selected_ids)
    rules_map = {rule.name: trial_rule}
    _, case_results = evaluate_rule(rule.name, cases, rules_map, backend)
    precision, recall = _compute_metrics(case_results)
    return precision, recall, case_results


def create_study(
    config: StudyConfig,
    sampler: optuna.samplers.BaseSampler | None = None,
) -> tuple[optuna.Study, str]:
    """Create a multi-objective Optuna study persisted to sqlite."""
    db_path = _study_db_path(config)
    storage = f"sqlite:///{db_path}"
    if sampler is None:
        sampler = optuna.samplers.TPESampler()
    study = optuna.create_study(
        study_name=config.rule_name,
        storage=storage,
        sampler=sampler,
        directions=["maximize", "maximize"],
    )
    return study, db_path


def run_trial(
    trial: optuna.Trial,
    rule: Rule,
    tune_cases: list[EvalCase],
    held_cases: list[EvalCase],
    backend: InferenceBackend,
    config: StudyConfig,
) -> tuple[float, float]:
    """Execute one Optuna trial. Returns (recall_held, precision_held)."""
    pool = _pool_ids(rule)
    if not pool:
        raise optuna.TrialPruned("No examples in pool")

    selected: list[str] = []
    for eid in pool:
        toggle = trial.suggest_categorical(eid, [0, 1])
        if toggle == 1:
            selected.append(eid)

    if not selected:
        raise optuna.TrialPruned("Empty selection")

    if not _check_prompt_budget(rule, selected):
        raise optuna.TrialPruned("Exceeds prompt budget")

    p_tune, r_tune, _ = _eval_subset(rule, selected, tune_cases, backend)
    p_held, r_held, _ = _eval_subset(rule, selected, held_cases, backend)

    trial.set_user_attr("precision_tune", p_tune)
    trial.set_user_attr("recall_tune", r_tune)
    trial.set_user_attr("example_ids", selected)

    if p_held < config.p_min:
        trial.set_user_attr("constraint_violated", True)

    return r_held, p_held


def best_trial_ids(study: optuna.Study) -> list[str] | None:
    """Extract example IDs from the best feasible trial."""
    try:
        best = study.best_trials
    except ValueError:
        return None
    if not best:
        return None
    return best[0].user_attrs.get("example_ids")
