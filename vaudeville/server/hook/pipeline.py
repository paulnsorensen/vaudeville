"""The hook decide pipeline: `handle_hook_request` (AC-5, AC-6, AC-16, AC-17, AC-21, AC-26).

Loads rules for the request `cwd`, filters by event and `matcher`, runs
decide agents, maps outcomes through `on:` under the tier ceiling, merges
actions by precedence, invokes the effects, renders through the adapter,
and appends a decision record per evaluated rule to the event log.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable, Mapping

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
    apply_rewrite,
    escalate,
    rewrite_or_feedback,
    run_named_command,
    with_origin_label,
)
from vaudeville.server.event_log import ClassificationEvent, EventLogger
from vaudeville.server.harness import HookEvent, Outcome, get_adapter
from vaudeville.server.user_config import UserConfig, load_user_config

from .precedence import EvaluatedAction, merge
from .tier import apply_tier_ceiling

logger = logging.getLogger(__name__)

# `escalate` is a one-hop, bounded operation; `run` is fire-and-forget.
DEFAULT_ESCALATE_DEADLINE_SECONDS = 3.0
RUN_COMMAND_TIMEOUT_SECONDS = 5.0

DecideFn = Callable[[DecideRule, UserConfig, str], DecideResult]

_GENERIC_ALLOW: dict[str, object] = {"stdout": "{}", "exit_code": 0}


def handle_hook_request(
    request: Mapping[str, object],
    *,
    config: UserConfig | None = None,
    event_logger: EventLogger | None = None,
    decide_fn: DecideFn | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, object]:
    """Answer `{op, harness, event, cwd, payload}` with `{stdout, exit_code}`.

    Fails open (allow, exit 0) on any exception, an unknown harness, or a
    handler error, per the invariant that the hook never blocks a session
    on an internal fault.
    """
    harness_name = str(request.get("harness", ""))
    adapter = get_adapter(harness_name)
    if adapter is None:
        return dict(_GENERIC_ALLOW)
    try:
        return _run_pipeline(
            request,
            adapter=adapter,
            config=config if config is not None else load_user_config(),
            event_logger=event_logger,
            decide_fn=decide_fn if decide_fn is not None else default_decide,
            clock=clock if clock is not None else time.monotonic,
        )
    except Exception:
        logger.exception("hook pipeline raised; allowing")
        return dict(_GENERIC_ALLOW)


def _run_pipeline(
    request: Mapping[str, object],
    *,
    adapter: object,
    config: UserConfig,
    event_logger: EventLogger | None,
    decide_fn: DecideFn,
    clock: Callable[[], float],
) -> dict[str, object]:
    payload = request.get("payload")
    raw = payload if isinstance(payload, Mapping) else {}
    event: HookEvent = adapter.normalize(raw)  # type: ignore[attr-defined]
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
        )
        if action is not None:
            evaluated.append(action)

    result = merge(evaluated)

    for dropped_action, reason in result.dropped:
        _log_dropped(event_logger, dropped_action, reason, prompt_chars=len(event.text))

    for run_action in result.run_actions:
        if run_action.command:
            run_named_command(
                run_action.command,
                config,
                event_json,
                timeout=RUN_COMMAND_TIMEOUT_SECONDS,
            )

    if result.primary is None:
        return dict(_GENERIC_ALLOW)

    outcome = Outcome(
        action=Action(action=result.primary.action_name),  # type: ignore[arg-type]
        message=result.primary.message,
        rule=result.primary.rule_name,
        event=event.event,
        updated_input=result.primary.updated_input,
        context=result.context,
    )
    rendered: dict[str, object] = adapter.render(outcome)  # type: ignore[attr-defined]
    return rendered


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

    start = clock()
    result = decide_fn(rule, config, event.text)
    latency_ms = (clock() - start) * 1000

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
    if action_name == "escalate":
        action_name, message, escalate_target, escalate_action = _do_escalate(
            rule, action_obj, by_name, event, config, decide_fn
        )
        if escalate_target is not None:
            dispatch_rule = escalate_target
            action_obj = escalate_action

    if action_name == "feedback":
        message = with_origin_label(dispatch_rule.name, message)

    if action_name == "rewrite":
        action_name, message, updated_input = _do_rewrite(
            dispatch_rule, action_obj, by_name, event, config, logger_fn
        )
    elif action_name == "add-context":
        context_text = action_obj.text if action_obj is not None else None
        context_text = context_text or message
    elif action_name == "run":
        command = action_obj.command if action_obj is not None else None

    effective_name, downgrade = apply_tier_ceiling(action_name, rule.tier)

    _log_decision(
        logger_fn,
        rule,
        result,
        action_name,
        downgrade,
        latency_ms,
        model_name,
        prompt_chars,
    )

    return EvaluatedAction(
        rule_name=rule.name,
        action_name=effective_name,
        message=message,
        context_text=context_text,
        updated_input=updated_input,
        command=command,
    )


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
) -> tuple[str, str, DecideRule | None, Action | None]:
    """Run the escalate target once and resolve its own `on:` mapping.

    Returns `(action_name, message, target_rule, target_action)`; the
    caller dispatches the remaining post-escalate branches against
    `target_rule`/`target_action` so an escalated rewrite, run, or
    feedback behaves as if the target fired directly. `target_rule` is
    None when escalation resolves to a plain allow (disabled target,
    deadline expiry, or an unmapped outcome).
    """
    target = by_name.get(action_obj.rule) if action_obj and action_obj.rule else None
    if not isinstance(target, DecideRule) or target.tier == "disabled":
        return "allow", "", None, None

    def _run() -> DecideResult:
        return decide_fn(target, config, event.text)

    escalated = escalate(
        _run, deadline=DEFAULT_ESCALATE_DEADLINE_SECONDS, rule_name=target.name
    )
    if escalated is None or escalated.outcome is None:
        return "allow", "", None, None

    target_action = target.on.get(escalated.outcome)
    if target_action is None:
        return "allow", "", None, None

    action_name, _ = apply_tier_ceiling(target_action.action, target.tier)
    message = _message_for(target, escalated, action_name)
    return action_name, message, target, target_action


def _do_rewrite(
    rule: DecideRule,
    action_obj: Action | None,
    by_name: dict[str, DecideRule | RewriteRule],
    event: HookEvent,
    config: UserConfig,
    logger_fn: EventLogger | None,
) -> tuple[str, str, dict[str, object] | None]:
    target = by_name.get(action_obj.rule) if action_obj and action_obj.rule else None
    if not isinstance(target, RewriteRule):
        return "allow", "", None
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
        return "allow", "", None

    resolution = resolve_model(target, config)
    if resolution.notice:
        print(resolution.notice, file=sys.stderr)
    if resolution.model is None:
        return "allow", "", None

    new_text = run_rewrite(target, resolution.model, event.text)
    if new_text is None:
        return "allow", "", None

    def _log(record: dict[str, object]) -> None:
        logger.info("rewrite effect for rule %r: %r", rule.name, record)

    downgraded = rewrite_or_feedback(event, new_text, rule_name=rule.name, log=_log)
    if downgraded is not None:
        labeled = with_origin_label(rule.name, new_text)
        return "feedback", labeled, None

    assert event.tool_input is not None
    new_values = {path: new_text for path in target.target}
    updated = apply_rewrite(
        event.tool_input, target.target, new_values, rule_name=rule.name, log=_log
    )
    return "rewrite", new_text, updated


def _log_dropped(
    logger_fn: EventLogger | None,
    dropped: EvaluatedAction,
    reason: str,
    *,
    prompt_chars: int,
) -> None:
    """Log a primary-channel action that lost the precedence merge (F20)."""
    if logger_fn is None:
        return
    logger_fn.log_event(
        ClassificationEvent(
            rule=dropped.rule_name,
            verdict="",
            confidence=0.0,
            latency_ms=0.0,
            prompt_chars=prompt_chars,
            action=dropped.action_name,
            downgrade=reason,
        )
    )


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
