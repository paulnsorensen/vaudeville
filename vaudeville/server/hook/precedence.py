"""Precedence merge across rules matching one event (AC-16).

The most restrictive of block > ask > rewrite > feedback > warn wins the
primary channel; every add-context, log, and run applies in addition, and
add-context text from multiple rules concatenates.
"""

from __future__ import annotations

from dataclasses import dataclass

# Most restrictive first.
PRIMARY_PRECEDENCE: tuple[str, ...] = ("block", "ask", "rewrite", "feedback", "warn")

SECONDARY_ACTIONS: frozenset[str] = frozenset({"add-context", "log", "run"})


@dataclass(frozen=True)
class EvaluatedAction:
    """One rule's resolved, tier-capped action, ready for precedence merge."""

    rule_name: str
    action_name: str
    message: str
    context_text: str | None = None
    updated_input: dict[str, object] | None = None
    command: str | None = None


@dataclass(frozen=True)
class MergeResult:
    """The primary winner (or None) plus the merged add-context text and run list."""

    primary: EvaluatedAction | None
    context: str | None
    run_actions: tuple[EvaluatedAction, ...]


def merge(evaluated: list[EvaluatedAction]) -> MergeResult:
    """Pick the most restrictive primary-channel action and merge the rest.

    `add-context` texts concatenate in rule order; every `run` applies in
    addition to whichever primary action wins.
    """
    primary: EvaluatedAction | None = None
    best_rank = len(PRIMARY_PRECEDENCE)
    contexts: list[str] = []
    run_actions: list[EvaluatedAction] = []

    for item in evaluated:
        if item.action_name in PRIMARY_PRECEDENCE:
            rank = PRIMARY_PRECEDENCE.index(item.action_name)
            if rank < best_rank:
                best_rank = rank
                primary = item
        elif item.action_name == "add-context":
            if item.context_text:
                contexts.append(item.context_text)
        elif item.action_name == "run":
            run_actions.append(item)

    if primary is None and contexts:
        primary = EvaluatedAction(
            rule_name=evaluated[0].rule_name if evaluated else "",
            action_name="add-context",
            message="\n\n".join(contexts),
            context_text="\n\n".join(contexts),
        )
        contexts = []

    return MergeResult(
        primary=primary,
        context="\n\n".join(contexts) if contexts else None,
        run_actions=tuple(run_actions),
    )
