"""Optuna study orchestration for rule tuning.

Creates and runs a multi-objective study that toggles example IDs
to optimize precision and recall on a held-out set.
"""

from __future__ import annotations

import difflib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import optuna

from ..core.rules import Rule, render_prompt
from ..eval import CaseResult, EvalCase, evaluate_rule
from ..server.inference import InferenceBackend
from .pool import (
    author_candidates,
    collect_fn_texts,
    compute_stall_count,
    inject_candidates,
    should_author,
)
from .sampler import LLMSampler

logger = logging.getLogger(__name__)

PROMPT_BUDGET = 2000


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


def _check_consecutive_hits(
    study: optuna.Study,
    config: StudyConfig,
) -> bool:
    """Check if the last N consecutive trials hit targets on held-out."""
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if len(completed) < config.consecutive_target:
        return False
    recent = completed[-config.consecutive_target :]
    return all(_trial_hits_targets(t, config) for t in recent)


def _trial_hits_targets(
    trial: optuna.trial.FrozenTrial,
    config: StudyConfig,
) -> bool:
    """Check if a single trial meets precision and recall targets."""
    if not trial.values or len(trial.values) < 2:
        return False
    r_held, p_held = float(trial.values[0]), float(trial.values[1])
    return p_held >= config.p_min and r_held >= config.r_min


def _write_prompt_diff(
    original: str,
    tuned: str,
    config: StudyConfig,
) -> str:
    """Write a prompt diff file. Returns the file path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    d = config.resolve_study_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{config.rule_name}-{ts}.diff")

    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        tuned.splitlines(keepends=True),
        fromfile="original",
        tofile="tuned",
    )
    with open(path, "w") as f:
        f.writelines(diff)
    return path


def _format_verdict(verdict: TuneVerdict) -> str:
    """Format the 8-12 line final verdict."""
    status = "PASS" if verdict.passed else "FAIL"
    lines = [
        f"=== vaudeville tune: {status} ===",
        "",
        f"Tune  — precision: {verdict.p_tune:.1%}  recall: {verdict.r_tune:.1%}",
        f"Held  — precision: {verdict.p_held:.1%}  recall: {verdict.r_held:.1%}",
        "",
        f"Trials: {verdict.trials_run}  Pool: {verdict.pool_size} examples",
        f"Best IDs: {', '.join(verdict.best_ids)}",
        f"Study: {verdict.study_uri}",
    ]
    if verdict.diff_path:
        lines.append(f"Diff: {verdict.diff_path}")
    return "\n".join(lines)


def _author_and_inject(
    rule: Rule,
    tune_cases: list[EvalCase],
    backend: InferenceBackend,
    best_ids: list[str],
) -> Rule:
    """Eval best selection for FNs, author new candidates, inject."""
    if not best_ids:
        return rule
    _, _, case_results = _eval_subset(rule, best_ids, tune_cases, backend)
    fn_texts = collect_fn_texts(case_results)
    if not fn_texts:
        return rule
    try:
        import anthropic

        client = anthropic.Anthropic()
    except Exception:
        logger.debug("Anthropic unavailable, skipping authoring")
        return rule
    existing_ids = _pool_ids(rule)
    new = author_candidates(client, rule.name, fn_texts, existing_ids)
    if new:
        logger.info("Authored %d new candidates", len(new))
        rule = inject_candidates(rule, new)
    return rule


def run_study(
    rule: Rule,
    tune_cases: list[EvalCase],
    held_cases: list[EvalCase],
    backend: InferenceBackend,
    config: StudyConfig,
    sampler: optuna.samplers.BaseSampler | None = None,
) -> TuneVerdict:
    """Run the full Optuna study loop. Returns a TuneVerdict."""
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study, db_path = create_study(config, sampler=sampler)
    original_prompt = render_prompt(rule)

    for i in range(config.budget):
        trial = study.ask()
        try:
            values = run_trial(
                trial,
                rule,
                tune_cases,
                held_cases,
                backend,
                config,
            )
            study.tell(trial, values=list(values))
        except optuna.TrialPruned as e:
            study.tell(trial, state=optuna.trial.TrialState.PRUNED)
            logger.debug("Trial %d pruned: %s", i, e)
            continue

        if config.author:
            completed = [
                t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
            ]
            vals = [list(t.values) for t in completed if t.values]
            stall = compute_stall_count(vals)
            if should_author(i, stall):
                ids = best_trial_ids(study) or []
                rule = _author_and_inject(
                    rule,
                    tune_cases,
                    backend,
                    ids,
                )

        if _check_consecutive_hits(study, config):
            logger.info(
                "Target hit %dx consecutively — stopping", config.consecutive_target
            )
            break

    best_ids = best_trial_ids(study) or []
    tuned_prompt = render_prompt(rule, best_ids) if best_ids else original_prompt
    diff_path = _write_prompt_diff(original_prompt, tuned_prompt, config)

    p_tune = r_tune = p_held = r_held = 0.0
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if completed:
        best = completed[-1]
        if best_ids:
            for t in reversed(completed):
                if t.user_attrs.get("example_ids") == best_ids:
                    best = t
                    break
        p_tune = best.user_attrs.get("precision_tune", 0.0)
        r_tune = best.user_attrs.get("recall_tune", 0.0)
        if best.values and len(best.values) >= 2:
            r_held, p_held = best.values[0], best.values[1]

    passed = p_held >= config.p_min and r_held >= config.r_min
    pool_size = len(_pool_ids(rule))
    study_uri = f"sqlite:///{db_path}"

    return TuneVerdict(
        passed=passed,
        p_tune=p_tune,
        r_tune=r_tune,
        p_held=p_held,
        r_held=r_held,
        trials_run=len(study.trials),
        pool_size=pool_size,
        best_ids=best_ids,
        study_uri=study_uri,
        diff_path=diff_path,
    )
