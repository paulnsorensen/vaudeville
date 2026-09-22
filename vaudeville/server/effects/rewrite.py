"""Rewrite effects: allowlisted tool-input mutation and the feedback downgrade.

The applier only ever changes dotted paths present in a `RewriteRule.target`
list; `vaudeville.rules` rejects a Bash-command target at load, so this
module never has to check for one (AC-8). When an event carries no tool
input, `rewrite_or_feedback` downgrades to feedback text instead (AC-9).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vaudeville.rules import Action
from vaudeville.server.harness import HookEvent, Outcome

logger = logging.getLogger(__name__)

_UNSET = object()
_TOOL_INPUT_PREFIX = "tool_input."


def _relative_path(path: str) -> str:
    """Strip the `tool_input.` prefix a target dotted path always carries."""
    if path.startswith(_TOOL_INPUT_PREFIX):
        return path[len(_TOOL_INPUT_PREFIX) :]
    return path


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _UNSET
        node = node[part]
    return node


def _set_path(data: dict[str, Any], path: str, value: object) -> None:
    """Set `path` on `data`, copying each traversed dict so callers' nested
    mappings are never mutated in place.

    When a segment must descend into an existing non-dict, non-null value
    (for example a Bash command string reached via a rejected-at-load
    subpath target that slipped through some other way), the whole set is
    skipped and logged instead of replacing that scalar with a dict.
    """
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if child is not None and not isinstance(child, dict):
            logger.warning(
                "rewrite: refusing to descend into %r; existing value is a %s, not a mapping",
                part,
                type(child).__name__,
            )
            return
        child = dict(child) if isinstance(child, dict) else {}
        node[part] = child
        node = child
    node[parts[-1]] = value


def apply_rewrite(
    tool_input: Mapping[str, Any],
    targets: Sequence[str],
    new_values: Mapping[str, object],
    *,
    rule_name: str,
    log: Callable[[dict[str, object]], None],
) -> dict[str, Any]:
    """Return `tool_input` with only the `targets` paths in `new_values` changed.

    A `new_values` key outside `targets` is ignored and logged. Each changed
    path logs `{rule, path, before, after}` via `log`.
    """
    updated: dict[str, Any] = dict(tool_input)
    allowed = set(targets)
    for path, after in new_values.items():
        if path not in allowed:
            logger.warning(
                "rewrite rule %r: ignoring new_values path %r not in target list",
                rule_name,
                path,
            )
            continue
        relative = _relative_path(path)
        before = _get_path(tool_input, relative)
        _set_path(updated, relative, after)
        log(
            {
                "rule": rule_name,
                "path": path,
                "before": None if before is _UNSET else before,
                "after": after,
            }
        )
    return updated


def rewrite_or_feedback(
    event: HookEvent,
    text: str,
    *,
    rule_name: str,
    log: Callable[[dict[str, object]], None],
) -> Outcome | None:
    """Downgrade a `rewrite` to `feedback` when `event` carries no tool input.

    Returns a `feedback` Outcome carrying `text` when `event.tool_input is
    None`, and logs `{from: "rewrite", to: "feedback", event}`. Returns
    None when the event has tool input, so the caller keeps rewriting.
    """
    if event.tool_input is not None:
        return None
    log({"from": "rewrite", "to": "feedback", "event": event.event, "rule": rule_name})
    return Outcome(
        action=Action(action="feedback"),
        message=text,
        rule=rule_name,
        event=event.event,
    )
