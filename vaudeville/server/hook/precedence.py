"""Precedence merge across rules matching one event (AC-16).

The most restrictive of block > ask > rewrite > feedback > warn wins the
primary channel; every add-context, log, and run applies in addition, and
add-context text from multiple rules concatenates. A primary-channel action
that loses to a more restrictive one is reported in `MergeResult.dropped` so
the caller can log it (F20).

Each `EvaluatedAction` also carries the decide result and model fields
needed to build its one events.jsonl row; the caller defers that row for
any action that could become (or lose to) the render primary, so a rule
never produces more than one row (F24).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from vaudeville.rules.policy import PRIMARY_PRECEDENCE, SECONDARY_ACTIONS


@dataclass(frozen=True)
class EvaluatedAction:
    """One rule's resolved, tier-capped action, ready for precedence merge."""

    rule_name: str
    action_name: str
    message: str
    context_text: str | None = None
    updated_input: dict[str, object] | None = None
    command: str | None = None
    downgrade: str | None = None
    verdict: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    reason: str = ""
    tier: str = "block"
    outcome: str | None = None
    model: str | None = None
    prompt_chars: int = 0


@dataclass(frozen=True)
class MergeResult:
    """The primary winner (or None) plus the merged add-context text and run list."""

    primary: EvaluatedAction | None
    context: str | None
    run_actions: tuple[EvaluatedAction, ...]
    dropped: tuple[tuple[EvaluatedAction, str], ...] = ()
    context_items: tuple[EvaluatedAction, ...] = ()


def merge(evaluated: list[EvaluatedAction]) -> MergeResult:
    """Pick the most restrictive primary-channel action and merge the rest.

    `add-context` texts concatenate in rule order; every `run` applies in
    addition to whichever primary action wins.
    """
    primary: EvaluatedAction | None = None
    best_rank = len(PRIMARY_PRECEDENCE)
    contexts: list[str] = []
    context_items: list[EvaluatedAction] = []
    run_actions: list[EvaluatedAction] = []
    primary_candidates: list[EvaluatedAction] = []

    for item in evaluated:
        if item.action_name in PRIMARY_PRECEDENCE:
            primary_candidates.append(item)
            rank = PRIMARY_PRECEDENCE.index(item.action_name)
            if rank < best_rank:
                best_rank = rank
                primary = item
        elif item.action_name == "add-context":
            if item.context_text:
                contexts.append(item.context_text)
                context_items.append(item)
        elif item.action_name == "run":
            run_actions.append(item)
        elif item.action_name in SECONDARY_ACTIONS:
            # `log`/`allow`/`escalate`: no merge-time effect; the per-rule
            # decision record already exists from the evaluate-time log.
            pass

    dropped = (
        tuple(
            (item, f"precedence:superseded-by:{primary.rule_name}")
            for item in primary_candidates
            if item is not primary
        )
        if primary is not None
        else ()
    )

    if primary is None and contexts:
        combined = "\n\n".join(contexts)
        base = context_items[0]
        primary = dataclasses.replace(base, message=combined, context_text=combined)
        contexts = []
        context_items = context_items[1:]

    return MergeResult(
        primary=primary,
        context="\n\n".join(contexts) if contexts else None,
        run_actions=tuple(run_actions),
        dropped=dropped,
        context_items=tuple(context_items),
    )
