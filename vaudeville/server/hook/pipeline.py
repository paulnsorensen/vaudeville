"""The hook decide pipeline: `handle_hook_request` (AC-5, AC-6, AC-16, AC-17, AC-21, AC-26).

Loads rules for the request `cwd`, filters by event and `matcher`, runs
decide agents, maps outcomes through `on:` under the tier ceiling, merges
actions by precedence, invokes the effects, renders through the adapter,
and appends a decision record per evaluated rule to the event log.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping

from vaudeville.core.protocol import GENERIC_ALLOW
from vaudeville.core.truncation import _truncate_for_event, prepare_text
from vaudeville.rules import (
    Action,
    DecideRule,
    RewriteRule,
    RuleSet,
)
from vaudeville.rules.cache import load_layered
from vaudeville.server.agents import DecideResult
from vaudeville.server.agents import decide as default_decide
from vaudeville.server.agents import resolve_model
from vaudeville.server.agents import rewrite as run_rewrite
from vaudeville.server.effects import (
    EscalateResult,
    apply_rewrite,
    escalate_result,
    rewrite_or_feedback,
    run_named_command,
    with_origin_label,
)
from vaudeville.server.event_log import ClassificationEvent, EventLogger
from vaudeville.server.harness import (
    Adapter,
    HookEvent,
    Outcome,
    RenderResult,
    get_adapter,
)
from vaudeville.server.user_config import UserConfig, load_user_config

from .precedence import EvaluatedAction, merge
from .tier import apply_tier_ceiling

logger = logging.getLogger(__name__)

# `escalate` is a one-hop, bounded operation; `run` is fire-and-forget.
DEFAULT_ESCALATE_DEADLINE_SECONDS = 3.0
RUN_COMMAND_TIMEOUT_SECONDS = 5.0
DEFAULT_REQUEST_DEADLINE_SECONDS = 6.0

# Actions whose decision record is complete at evaluate time: never a
# precedence-merge primary or an add-context concatenation, so nothing
# downstream can add a second row for the same rule (F24).
_NO_DEFER = frozenset({"run", "allow", "log"})

DecideFn = Callable[[DecideRule, UserConfig, str], DecideResult]


def handle_hook_request(
    request: Mapping[str, object],
    *,
    config: UserConfig | None = None,
    event_logger: EventLogger | None = None,
    decide_fn: DecideFn | None = None,
    clock: Callable[[], float] | None = None,
    deadline_seconds: float = DEFAULT_REQUEST_DEADLINE_SECONDS,
) -> dict[str, object]:
    """Answer `{op, harness, event, cwd, payload}` with `{stdout, exit_code}`.

    Fails open (allow, exit 0) on any exception, an unknown harness, or a
    handler error, per the invariant that the hook never blocks a session
    on an internal fault. `deadline_seconds` bounds the whole request; every
    `decide_fn` call runs through `escalate` with the remaining budget, and
    an expired budget fails open with a `decide-timeout` downgrade record.
    """
    harness_name = str(request.get("harness", ""))
    adapter = get_adapter(harness_name)
    if adapter is None:
        return dict(GENERIC_ALLOW)
    try:
        return _run_pipeline(
            request,
            adapter=adapter,
            config=config if config is not None else load_user_config(),
            event_logger=event_logger,
            decide_fn=decide_fn if decide_fn is not None else default_decide,
            clock=clock if clock is not None else time.monotonic,
            deadline_seconds=deadline_seconds,
        )
    except Exception:
        logger.exception("hook pipeline raised; allowing")
        return _to_wire(adapter.render_allow())


def _remaining_budget(
    clock: Callable[[], float], start_time: float, deadline_seconds: float
) -> float:
    return max(0.0, deadline_seconds - (clock() - start_time))


def _to_wire(rendered: RenderResult) -> dict[str, object]:
    """Strip the render's own `downgrades` before it reaches the wire."""
    return {"stdout": rendered["stdout"], "exit_code": rendered["exit_code"]}


def _run_pipeline(
    request: Mapping[str, object],
    *,
    adapter: Adapter,
    config: UserConfig,
    event_logger: EventLogger | None,
    decide_fn: DecideFn,
    clock: Callable[[], float],
    deadline_seconds: float,
) -> dict[str, object]:
    start_time = clock()
    payload = request.get("payload")
    raw = payload if isinstance(payload, Mapping) else {}
    event: HookEvent = adapter.normalize(raw)
    text = _truncate_for_event(prepare_text(event.text, event.event), event.event)
    event = event.model_copy(update={"text": text})

    ruleset: RuleSet = load_layered(event.cwd)
    by_name = ruleset.by_name()
    matching = [
        rule
        for rule in ruleset.for_event(event.event)
        if isinstance(rule, DecideRule)
        and _matcher_matches(rule.matcher, event.tool_name)
    ]

    evaluated: list[EvaluatedAction] = []
    event_json = json.dumps(dict(raw))
    decide_memo: dict[tuple[str, str], tuple[EscalateResult[DecideResult], float]] = {}

    for rule in matching:
        if rule.tier == "disabled":
            continue
        action = _evaluate_rule(
            rule,
            by_name=by_name,
            event=event,
            config=config,
            decide_fn=decide_fn,
            clock=clock,
            logger_fn=event_logger,
            prompt_chars=len(event.text),
            start_time=start_time,
            deadline_seconds=deadline_seconds,
            decide_memo=decide_memo,
        )
        if action is not None:
            evaluated.append(action)

    result = merge(evaluated)

    for dropped_action, reason in result.dropped:
        _log_dropped(event_logger, dropped_action, reason)

    for item in result.context_items:
        _log_evaluated(event_logger, item, downgrade=item.downgrade)

    for run_action in result.run_actions:
        if run_action.command:
            run_named_command(
                run_action.command,
                config,
                event_json,
                timeout=RUN_COMMAND_TIMEOUT_SECONDS,
            )

    if result.primary is None:
        return _to_wire(adapter.render_allow())

    # The rendered action is already resolved; it carries no rule parameter.
    outcome = Outcome(
        action=Action.model_construct(action=result.primary.action_name),
        message=result.primary.message,
        rule=result.primary.rule_name,
        event=event.event,
        updated_input=result.primary.updated_input,
        context=result.context,
    )
    rendered: RenderResult = adapter.render(outcome)
    _log_evaluated(
        event_logger,
        result.primary,
        downgrade=_merge_downgrade(result.primary.downgrade, rendered["downgrades"]),
    )
    return _to_wire(rendered)


def _matcher_matches(matcher: str | None, tool_name: str | None) -> bool:
    if matcher is None:
        return True
    return tool_name in matcher.split("|")


def _evaluate_rule(
    rule: DecideRule,
    *,
    by_name: dict[str, DecideRule | RewriteRule],
    event: HookEvent,
    config: UserConfig,
    decide_fn: DecideFn,
    clock: Callable[[], float],
    logger_fn: EventLogger | None,
    prompt_chars: int,
    start_time: float,
    deadline_seconds: float,
    decide_memo: dict[tuple[str, str], tuple[EscalateResult[DecideResult], float]],
) -> EvaluatedAction | None:
    model_name = rule.model or config.default_model

    if not event.text:
        _log_decision(
            logger_fn,
            rule,
            DecideResult(outcome=None),
            "allow",
            None,
            0.0,
            model_name,
            prompt_chars,
        )
        return None

    memo_key = (rule.name, event.text)
    cached = decide_memo.get(memo_key)
    if cached is not None:
        decide_outcome, latency_ms = cached
    else:
        remaining = _remaining_budget(clock, start_time, deadline_seconds)
        start = clock()
        decide_outcome = escalate_result(
            lambda: decide_fn(rule, config, event.text),
            deadline=remaining,
            rule_name=rule.name,
        )
        latency_ms = (clock() - start) * 1000
        if decide_outcome.value is not None:
            decide_memo[memo_key] = (decide_outcome, latency_ms)

    if decide_outcome.value is None:
        decide_downgrade = (
            "decide-error" if decide_outcome.error is not None else "decide-timeout"
        )
        _log_decision(
            logger_fn,
            rule,
            DecideResult(outcome=None),
            "allow",
            decide_downgrade,
            latency_ms,
            model_name,
            prompt_chars,
        )
        return None
    result = decide_outcome.value

    if result.outcome is None:
        _log_decision(
            logger_fn, rule, result, "allow", None, latency_ms, model_name, prompt_chars
        )
        return None

    action_obj = rule.on.get(result.outcome)
    action_name: str = action_obj.action if action_obj is not None else "allow"
    message = _message_for(rule, result, action_name)

    updated_input: dict[str, object] | None = None
    context_text: str | None = None
    command: str | None = None

    dispatch_rule = rule
    escalate_ceiling_reason: str | None = None
    if action_name == "escalate":
        remaining = _remaining_budget(clock, start_time, deadline_seconds)
        (
            action_name,
            message,
            escalate_target,
            escalate_action,
            escalate_ceiling_reason,
        ) = _do_escalate(
            rule,
            action_obj,
            by_name,
            event,
            config,
            decide_fn,
            remaining,
            decide_memo,
            clock,
        )
        if escalate_target is not None:
            dispatch_rule = escalate_target
            action_obj = escalate_action

    if action_name == "feedback":
        message = with_origin_label(dispatch_rule.name, message)

    rewrite_downgrade: str | None = None
    if action_name == "rewrite":
        action_name, message, updated_input, rewrite_downgrade = _do_rewrite(
            dispatch_rule,
            action_obj,
            by_name,
            event,
            config,
            logger_fn,
            clock=clock,
            start_time=start_time,
            deadline_seconds=deadline_seconds,
        )
    elif action_name == "add-context":
        context_text = action_obj.text if action_obj is not None else None
        context_text = context_text or message
    elif action_name == "run":
        command = action_obj.command if action_obj is not None else None

    effective_name, downgrade = apply_tier_ceiling(action_name, rule.tier)
    extra_downgrades = [d for d in (escalate_ceiling_reason, rewrite_downgrade) if d]
    if extra_downgrades:
        downgrade = (
            f"{';'.join(extra_downgrades)};{downgrade}"
            if downgrade
            else ";".join(extra_downgrades)
        )

    item = EvaluatedAction(
        rule_name=rule.name,
        action_name=effective_name,
        message=message,
        context_text=context_text,
        updated_input=updated_input,
        command=command,
        downgrade=downgrade,
        verdict=result.outcome or "",
        confidence=result.confidence or 0.0,
        latency_ms=latency_ms,
        reason=result.reason or "",
        tier=rule.tier,
        outcome=result.outcome,
        model=model_name,
        prompt_chars=prompt_chars,
    )
    if effective_name in _NO_DEFER:
        _log_evaluated(logger_fn, item, downgrade=downgrade)

    return item


def _message_for(rule: DecideRule, result: DecideResult, action_name: str) -> str:
    if action_name in ("warn", "block") and rule.reasons:
        if result.reason and result.reason in rule.reasons:
            return rule.reasons[result.reason]
        return result.outcome or action_name
    return result.reason or result.outcome or ""


def _do_escalate(
    rule: DecideRule,
    action_obj: Action | None,
    by_name: dict[str, DecideRule | RewriteRule],
    event: HookEvent,
    config: UserConfig,
    decide_fn: DecideFn,
    remaining_budget: float,
    decide_memo: dict[tuple[str, str], tuple[EscalateResult[DecideResult], float]],
    clock: Callable[[], float],
) -> tuple[str, str, DecideRule | None, Action | None, str | None]:
    """Run the escalate target once and resolve its own `on:` mapping.

    Returns `(action_name, message, target_rule, target_action, ceiling_reason)`;
    the caller dispatches the remaining post-escalate branches against
    `target_rule`/`target_action` so an escalated rewrite, run, or
    feedback behaves as if the target fired directly. `target_rule` is
    None when escalation resolves to a plain allow (disabled target,
    deadline expiry, or an unmapped outcome). `ceiling_reason` carries the
    target's own tier downgrade (e.g. `tier:warn`) for the caller's record.

    `decide_memo`, keyed by `(rule.name, event.text)`, reuses a decide
    result already computed this request (by the main loop or an earlier
    escalate hop) instead of calling `decide_fn` again, so a rule never
    decides more than once per request and an escalation never
    double-charges the request deadline. Only a successful decide
    (`result.value is not None`) is cached, with its recorded latency; a
    timeout or error is never cached, so a later turn re-runs the rule
    on its own full remaining budget instead of reusing a failure.
    """
    target = by_name.get(action_obj.rule) if action_obj and action_obj.rule else None
    if not isinstance(target, DecideRule):
        _warn_unresolved(rule, action_obj, "escalate")
        return "allow", "", None, None, None
    if target.tier == "disabled":
        return "allow", "", None, None, None
    if target.event != event.event or not _matcher_matches(
        target.matcher, event.tool_name
    ):
        logger.warning(
            "escalate rule %r targets %r: event/matcher mismatch against live "
            "event %r/%r; allowing",
            rule.name,
            target.name,
            event.event,
            event.tool_name,
        )
        return "allow", "", None, None, None

    memo_key = (target.name, event.text)
    cached = decide_memo.get(memo_key)
    if cached is not None:
        escalated_result, _ = cached
    else:

        def _run() -> DecideResult:
            return decide_fn(target, config, event.text)

        start = clock()
        escalated_result = escalate_result(
            _run,
            deadline=min(DEFAULT_ESCALATE_DEADLINE_SECONDS, remaining_budget),
            rule_name=target.name,
        )
        latency_ms = (clock() - start) * 1000
        if escalated_result.value is not None:
            decide_memo[memo_key] = (escalated_result, latency_ms)

    escalated = escalated_result.value
    if escalated is None or escalated.outcome is None:
        return "allow", "", None, None, None

    target_action = target.on.get(escalated.outcome)
    if target_action is None:
        return "allow", "", None, None, None

    action_name, reason = apply_tier_ceiling(target_action.action, target.tier)
    message = _message_for(target, escalated, action_name)
    return action_name, message, target, target_action, reason


def _warn_unresolved(rule: DecideRule, action_obj: Action | None, kind: str) -> None:
    logger.warning(
        "%s rule %r: target %r does not resolve to a %s rule; allowing",
        kind,
        rule.name,
        action_obj.rule if action_obj else None,
        "decide" if kind == "escalate" else "rewrite",
    )


def _do_rewrite(
    rule: DecideRule,
    action_obj: Action | None,
    by_name: dict[str, DecideRule | RewriteRule],
    event: HookEvent,
    config: UserConfig,
    logger_fn: EventLogger | None,
    *,
    clock: Callable[[], float],
    start_time: float,
    deadline_seconds: float,
) -> tuple[str, str, dict[str, object] | None, str | None]:
    target = by_name.get(action_obj.rule) if action_obj and action_obj.rule else None
    if not isinstance(target, RewriteRule):
        _warn_unresolved(rule, action_obj, "rewrite")
        return "allow", "", None, None
    if target.tier == "disabled":
        return "allow", "", None, None
    if target.event != event.event or not _matcher_matches(
        target.matcher, event.tool_name
    ):
        logger.warning(
            "rewrite rule %r targets %r: event/matcher mismatch against live "
            "event %r/%r; allowing",
            rule.name,
            target.name,
            event.event,
            event.tool_name,
        )
        return "allow", "", None, None

    resolution = resolve_model(target, config)
    if resolution.model is None:
        return "allow", "", None, None
    model = resolution.model

    remaining = _remaining_budget(clock, start_time, deadline_seconds)
    rewrite_outcome = escalate_result(
        lambda: run_rewrite(target, model, event.text),
        deadline=remaining,
        rule_name=target.name,
    )
    if rewrite_outcome.value is None:
        downgrade = (
            "rewrite-error" if rewrite_outcome.error is not None else "rewrite-timeout"
        )
        return "allow", "", None, downgrade
    new_text = rewrite_outcome.value

    def _log(record: dict[str, object]) -> None:
        logger.info("rewrite effect for rule %r: %r", rule.name, record)

    downgraded = rewrite_or_feedback(event, new_text, rule_name=rule.name, log=_log)
    if downgraded is not None:
        labeled = with_origin_label(rule.name, new_text)
        return "feedback", labeled, None, None

    assert event.tool_input is not None
    new_values = {path: new_text for path in target.target}
    updated = apply_rewrite(
        event.tool_input, target.target, new_values, rule_name=rule.name, log=_log
    )
    action_name, reason = apply_tier_ceiling("rewrite", target.tier)
    if action_name != "rewrite":
        return action_name, new_text, None, reason
    return "rewrite", new_text, updated, None


def _merge_downgrade(
    primary_downgrade: str | None, render_downgrades: list[dict[str, str]]
) -> str | None:
    """Join the primary's tier downgrade with the adapter's render downgrades.

    Both survive in one `;`-joined record, primary first (F24).
    """
    rendered = ";".join(
        f"{d.get('from')}->{d.get('to')} on {d.get('event')}" for d in render_downgrades
    )
    if primary_downgrade and rendered:
        return f"{primary_downgrade};{rendered}"
    return primary_downgrade or rendered or None


def _log_evaluated(
    logger_fn: EventLogger | None,
    item: EvaluatedAction,
    *,
    downgrade: str | None,
    kind: str | None = None,
) -> None:
    """Log one rule's decision record, once, from its `EvaluatedAction` (F24)."""
    if logger_fn is None:
        return
    logger_fn.log_event(
        ClassificationEvent(
            rule=item.rule_name,
            verdict=item.verdict,
            confidence=item.confidence,
            latency_ms=item.latency_ms,
            prompt_chars=item.prompt_chars,
            reason=item.reason,
            tier=item.tier,
            outcome=item.outcome,
            action=item.action_name,
            model=item.model,
            downgrade=downgrade,
            kind=kind,
        )
    )


def _log_dropped(
    logger_fn: EventLogger | None,
    dropped: EvaluatedAction,
    reason: str,
) -> None:
    """Log a primary-channel action that lost the precedence merge (F20).

    Joins the precedence reason onto the item's own downgrade (e.g. a
    target tier ceiling) instead of overwriting it.
    """
    downgrade = f"{dropped.downgrade};{reason}" if dropped.downgrade else reason
    _log_evaluated(logger_fn, dropped, downgrade=downgrade, kind="dropped")


def _log_decision(
    logger_fn: EventLogger | None,
    rule: DecideRule,
    result: DecideResult,
    action_name: str,
    downgrade: str | None,
    latency_ms: float,
    model_name: str | None,
    prompt_chars: int,
) -> None:
    if logger_fn is None:
        return
    logger_fn.log_event(
        ClassificationEvent(
            rule=rule.name,
            verdict=result.outcome or "",
            confidence=result.confidence or 0.0,
            latency_ms=latency_ms,
            prompt_chars=prompt_chars,
            reason=result.reason or "",
            tier=rule.tier,
            outcome=result.outcome,
            action=action_name,
            model=model_name,
            downgrade=downgrade,
        )
    )
