"""Optuna study orchestration for rule tuning."""

from __future__ import annotations

import difflib
import logging
import os
from datetime import datetime, timezone

import optuna

from ..core.rules import Rule, render_prompt
from ..eval import CaseResult, EvalCase, evaluate_rule
from ..server import InferenceBackend
from .pool import (
    author_candidates,
    collect_fn_texts,
    compute_stall_count,
    inject_candidates,
    should_author,
)
from .study import StudyConfig, TrialContext, TuneVerdict, create_study

logger = logging.getLogger(__name__)

PROMPT_BUDGET = 2000


def _pool_ids(rule: Rule) -> list[str]:
    return [ex.id for ex in rule.examples + rule.candidates]


def _compute_metrics(
    case_results: list[CaseResult],
) -> tuple[float, float]:
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
    rendered = render_prompt(rule, ids)
    return len(rendered) <= PROMPT_BUDGET


def _make_trial_rule(rule: Rule, selected_ids: list[str]) -> Rule:
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
    trial_rule = _make_trial_rule(rule, selected_ids)
    rules_map = {rule.name: trial_rule}
    _, case_results = evaluate_rule(rule.name, cases, rules_map, backend)
    precision, recall = _compute_metrics(case_results)
    return precision, recall, case_results


def run_trial(
    trial: optuna.Trial,
    ctx: TrialContext,
) -> tuple[float, float]:
    pool = _pool_ids(ctx.rule)
    if not pool:
        raise optuna.TrialPruned("No examples in pool")

    selected: list[str] = []
    for eid in pool:
        toggle = trial.suggest_categorical(eid, [0, 1])
        if toggle == 1:
            selected.append(eid)

    if not selected:
        raise optuna.TrialPruned("Empty selection")

    if not _check_prompt_budget(ctx.rule, selected):
        raise optuna.TrialPruned("Exceeds prompt budget")

    p_tune, r_tune, _ = _eval_subset(ctx.rule, selected, ctx.tune_cases, ctx.backend)
    p_held, r_held, _ = _eval_subset(ctx.rule, selected, ctx.held_cases, ctx.backend)

    trial.set_user_attr("precision_tune", p_tune)
    trial.set_user_attr("recall_tune", r_tune)
    trial.set_user_attr("example_ids", selected)

    if p_held < ctx.config.p_min:
        trial.set_user_attr("constraint_violated", True)

    return r_held, p_held


def best_trial_ids(study: optuna.Study) -> list[str] | None:
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
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if len(completed) < config.consecutive_target:
        return False
    recent = completed[-config.consecutive_target :]
    return all(_trial_hits_targets(t, config) for t in recent)


def _trial_hits_targets(
    trial: optuna.trial.FrozenTrial,
    config: StudyConfig,
) -> bool:
    if not trial.values or len(trial.values) < 2:
        return False
    r_held, p_held = float(trial.values[0]), float(trial.values[1])
    return p_held >= config.p_min and r_held >= config.r_min


def _write_prompt_diff(
    original: str,
    tuned: str,
    config: StudyConfig,
) -> str:
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


def format_verdict(verdict: TuneVerdict) -> str:
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


def _find_best_completed(
    completed: list[optuna.trial.FrozenTrial],
    best_ids: list[str],
) -> optuna.trial.FrozenTrial:
    for t in reversed(completed):
        if t.user_attrs.get("example_ids") == best_ids:
            return t
    return completed[-1]


def _extract_best_result(
    completed: list[optuna.trial.FrozenTrial],
    best_ids: list[str],
) -> tuple[float, float, float, float]:
    if not completed:
        return 0.0, 0.0, 0.0, 0.0
    best = _find_best_completed(completed, best_ids) if best_ids else completed[-1]
    p_tune = best.user_attrs.get("precision_tune", 0.0)
    r_tune = best.user_attrs.get("recall_tune", 0.0)
    r_held = p_held = 0.0
    if best.values and len(best.values) >= 2:
        r_held, p_held = best.values[0], best.values[1]
    return p_tune, r_tune, p_held, r_held


def _run_study_loop(
    study: optuna.Study,
    ctx: TrialContext,
) -> None:
    for i in range(ctx.config.budget):
        trial = study.ask()
        try:
            values = run_trial(trial, ctx)
            study.tell(trial, values=list(values))
        except optuna.TrialPruned as e:
            study.tell(trial, state=optuna.trial.TrialState.PRUNED)
            logger.debug("Trial %d pruned: %s", i, e)
            continue

        if ctx.config.author:
            completed = [
                t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
            ]
            vals = [list(t.values) for t in completed if t.values]
            stall = compute_stall_count(vals)
            if should_author(i, stall):
                ids = best_trial_ids(study) or []
                ctx.rule = _author_and_inject(
                    ctx.rule, ctx.tune_cases, ctx.backend, ids
                )

        if _check_consecutive_hits(study, ctx.config):
            logger.info(
                "Target hit %dx consecutively — stopping",
                ctx.config.consecutive_target,
            )
            break


def run_study(
    ctx: TrialContext,
    sampler: optuna.samplers.BaseSampler | None = None,
) -> TuneVerdict:
    """Run the full Optuna study loop. Returns a TuneVerdict."""
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study, db_path = create_study(ctx.config, sampler=sampler)
    original_prompt = render_prompt(ctx.rule)

    _run_study_loop(study, ctx)

    best_ids = best_trial_ids(study) or []
    tuned = render_prompt(ctx.rule, best_ids) if best_ids else original_prompt
    diff_path = _write_prompt_diff(original_prompt, tuned, ctx.config)

    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    p_tune, r_tune, p_held, r_held = _extract_best_result(completed, best_ids)

    return TuneVerdict(
        passed=p_held >= ctx.config.p_min and r_held >= ctx.config.r_min,
        p_tune=p_tune,
        r_tune=r_tune,
        p_held=p_held,
        r_held=r_held,
        trials_run=len(study.trials),
        pool_size=len(_pool_ids(ctx.rule)),
        best_ids=best_ids,
        study_uri=f"sqlite:///{db_path}",
        diff_path=diff_path,
    )
