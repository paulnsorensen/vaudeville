"""Claude Code adapter: renders the ten-action vocabulary as hook JSON.

Output shapes follow the Claude Code hooks reference
(https://code.claude.com/docs/en/hooks, read 2026-09-22). An action that has
no channel on a given event degrades to `warn` and the downgrade is recorded
on `self.downgrades` for the pipeline to log (AC-7).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from vaudeville.server.harness import HookEvent, Outcome

# hookSpecificOutput.permissionDecision (allow/deny/ask) and updatedInput
# exist only on PreToolUse.
_PERMISSION_DECISION_EVENTS = frozenset({"PreToolUse"})

# Top-level `decision: "block"` + `reason` events, per the Decision control
# table's "Top-level decision" row.
_TOP_LEVEL_DECISION_EVENTS = frozenset(
    {
        "UserPromptSubmit",
        "UserPromptExpansion",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "Stop",
        "SubagentStop",
        "ConfigChange",
        "PreCompact",
    }
)

# hookSpecificOutput.additionalContext exists on these events ("Context
# only" row plus the events named under "Add context for Claude").
_ADDITIONAL_CONTEXT_EVENTS = frozenset(
    {
        "SessionStart",
        "SubagentStart",
        "PostModelSwitch",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "Stop",
        "SubagentStop",
    }
)

_TEXT_FIELDS_BY_EVENT: dict[str, tuple[str, ...]] = {
    "UserPromptSubmit": ("prompt",),
    "UserPromptExpansion": ("prompt",),
    "Stop": ("last_assistant_message",),
    "SubagentStop": ("last_assistant_message",),
}

_TOOL_INPUT_TEXT_FIELDS = ("command", "content", "new_string", "prompt", "body")


def _derive_text(event: str, raw: Mapping[str, object]) -> str:
    """Best-effort classifiable text for an event."""
    for field in _TEXT_FIELDS_BY_EVENT.get(event, ()):
        value = raw.get(field)
        if isinstance(value, str) and value:
            return value
    tool_input = raw.get("tool_input")
    if isinstance(tool_input, dict):
        for key in _TOOL_INPUT_TEXT_FIELDS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
    tool_response = raw.get("tool_response")
    if isinstance(tool_response, str) and tool_response:
        return tool_response
    return ""


def _json(payload: dict[str, object]) -> dict[str, object]:
    return {"stdout": json.dumps(payload), "exit_code": 0}


class ClaudeCodeAdapter:
    """Adapter for the Claude Code harness."""

    def __init__(self) -> None:
        self.downgrades: list[dict[str, str]] = []

    def normalize(self, raw: Mapping[str, object]) -> HookEvent:
        event = str(raw.get("hook_event_name", ""))
        tool_input = raw.get("tool_input")
        tool_name = raw.get("tool_name")
        return HookEvent(
            harness="claude-code",
            event=event,
            tool_name=tool_name if isinstance(tool_name, str) else None,
            tool_input=tool_input if isinstance(tool_input, dict) else None,
            cwd=str(raw.get("cwd", "")),
            raw=dict(raw),
            text=_derive_text(event, raw),
        )

    def render(self, outcome: Outcome) -> dict[str, object]:
        name = outcome.action.action
        renderers: dict[str, Callable[[Outcome], dict[str, object]]] = {
            "allow": self._render_allow,
            "log": self._render_log,
            "warn": self._render_warn,
            "block": self._render_block,
            "feedback": self._render_feedback,
            "rewrite": self._render_rewrite,
            "escalate": self._render_escalate,
            "ask": self._render_ask,
            "add-context": self._render_add_context,
            "run": self._render_run,
        }
        method = renderers.get(name)
        if method is None:
            return self._degrade(outcome, name)
        return method(outcome)

    def _degrade(
        self, outcome: Outcome, action_name: str, text: str | None = None
    ) -> dict[str, object]:
        self.downgrades.append(
            {"from": action_name, "to": "warn", "event": outcome.event}
        )
        return _json({"systemMessage": text if text is not None else outcome.message})

    def render_allow(self) -> dict[str, object]:
        return {"stdout": "{}", "exit_code": 0}

    def _render_allow(self, outcome: Outcome) -> dict[str, object]:
        return self.render_allow()

    # `log`'s side effect belongs to the pipeline; the hook output is allow.
    _render_log = _render_allow

    # The pipeline resolves `escalate` before render is reached; a defensive
    # fallback here renders it as allow.
    _render_escalate = _render_allow

    # `run` starts a fire-and-forget process in the effects layer; the hook
    # itself must not block.
    _render_run = _render_allow

    def _render_warn(self, outcome: Outcome) -> dict[str, object]:
        return _json({"systemMessage": outcome.message})

    def _render_block(self, outcome: Outcome) -> dict[str, object]:
        event = outcome.event
        if event in _PERMISSION_DECISION_EVENTS:
            return _json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "permissionDecision": "deny",
                        "permissionDecisionReason": outcome.message,
                    }
                }
            )
        if event in _TOP_LEVEL_DECISION_EVENTS:
            return _json({"decision": "block", "reason": outcome.message})
        return self._degrade(outcome, "block")

    def _render_ask(self, outcome: Outcome) -> dict[str, object]:
        if outcome.event in _PERMISSION_DECISION_EVENTS:
            return _json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": outcome.event,
                        "permissionDecision": "ask",
                        "permissionDecisionReason": outcome.message,
                    }
                }
            )
        return self._degrade(outcome, "ask")

    def _render_rewrite(self, outcome: Outcome) -> dict[str, object]:
        if (
            outcome.event in _PERMISSION_DECISION_EVENTS
            and outcome.updated_input is not None
        ):
            return _json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": outcome.event,
                        "permissionDecision": "allow",
                        "updatedInput": outcome.updated_input,
                    }
                }
            )
        return self._degrade(outcome, "rewrite")

    def _render_feedback(self, outcome: Outcome) -> dict[str, object]:
        return self._render_context_message(outcome, outcome.message, "feedback")

    def _render_add_context(self, outcome: Outcome) -> dict[str, object]:
        text = outcome.context if outcome.context is not None else outcome.message
        return self._render_context_message(outcome, text, "add-context")

    def _render_context_message(
        self, outcome: Outcome, text: str, action_name: str
    ) -> dict[str, object]:
        if outcome.event in _ADDITIONAL_CONTEXT_EVENTS:
            return _json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": outcome.event,
                        "additionalContext": text,
                    }
                }
            )
        return self._degrade(outcome, action_name, text=text)
