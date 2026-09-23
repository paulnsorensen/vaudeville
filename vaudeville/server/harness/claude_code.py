"""Claude Code adapter: renders the ten-action vocabulary as hook JSON.

Output shapes follow the Claude Code hooks reference
(https://code.claude.com/docs/en/hooks, read 2026-09-22). An action that has
no channel on a given event degrades to `warn` and the downgrade is recorded
in the render result's `downgrades` list for the pipeline to log (AC-7).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from vaudeville.server.harness import HookEvent, Outcome, RenderResult

_RenderStep = Callable[[Outcome], tuple[dict[str, object], dict[str, str] | None]]

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


def _with_additional_context(stdout: str, event: str, context: str) -> str:
    """Put merged add-context text beside a primary action's payload (AC-16)."""
    body = json.loads(stdout)
    specific = body.setdefault("hookSpecificOutput", {"hookEventName": event})
    existing = specific.get("additionalContext")
    specific["additionalContext"] = f"{existing}\n\n{context}" if existing else context
    return json.dumps(body)


class ClaudeCodeAdapter:
    """Adapter for the Claude Code harness."""

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

    def render(self, outcome: Outcome) -> RenderResult:
        name = outcome.action.action
        renderers: dict[str, _RenderStep] = {
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
        payload, downgrade = (
            self._degrade(outcome, name) if method is None else method(outcome)
        )
        downgrades = [downgrade] if downgrade is not None else []
        stdout = str(payload["stdout"])
        if outcome.context and name != "add-context":
            if outcome.event in _ADDITIONAL_CONTEXT_EVENTS:
                stdout = _with_additional_context(
                    stdout, outcome.event, outcome.context
                )
            else:
                downgrades.append(
                    {"from": "add-context", "to": "dropped", "event": outcome.event}
                )
        return {
            "stdout": stdout,
            "exit_code": int(payload["exit_code"]),  # type: ignore[call-overload]
            "downgrades": downgrades,
        }

    def _degrade(
        self, outcome: Outcome, action_name: str, text: str | None = None
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        downgrade = {"from": action_name, "to": "warn", "event": outcome.event}
        payload = _json(
            {"systemMessage": text if text is not None else outcome.message}
        )
        return payload, downgrade

    def render_allow(self) -> RenderResult:
        return {"stdout": "{}", "exit_code": 0, "downgrades": []}

    def _render_allow(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        del outcome
        return {"stdout": "{}", "exit_code": 0}, None

    # `log`'s side effect belongs to the pipeline; the hook output is allow.
    _render_log = _render_allow

    # The pipeline resolves `escalate` before render is reached; a defensive
    # fallback here renders it as allow.
    _render_escalate = _render_allow

    # `run` starts a fire-and-forget process in the effects layer; the hook
    # itself must not block.
    _render_run = _render_allow

    def _render_warn(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        return _json({"systemMessage": outcome.message}), None

    def _render_block(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        event = outcome.event
        if event in _PERMISSION_DECISION_EVENTS:
            return (
                _json(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": event,
                            "permissionDecision": "deny",
                            "permissionDecisionReason": outcome.message,
                        }
                    }
                ),
                None,
            )
        if event in _TOP_LEVEL_DECISION_EVENTS:
            return _json({"decision": "block", "reason": outcome.message}), None
        return self._degrade(outcome, "block")

    def _render_ask(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        if outcome.event in _PERMISSION_DECISION_EVENTS:
            return (
                _json(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": outcome.event,
                            "permissionDecision": "ask",
                            "permissionDecisionReason": outcome.message,
                        }
                    }
                ),
                None,
            )
        return self._degrade(outcome, "ask")

    def _render_rewrite(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        # `ask` shows the changed input for approval; a rewrite never grants
        # more permission than the unmodified call gets.
        if (
            outcome.event in _PERMISSION_DECISION_EVENTS
            and outcome.updated_input is not None
        ):
            return (
                _json(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": outcome.event,
                            "permissionDecision": "ask",
                            "updatedInput": outcome.updated_input,
                        }
                    }
                ),
                None,
            )
        return self._degrade(outcome, "rewrite")

    def _render_feedback(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        return self._render_context_message(outcome, outcome.message, "feedback")

    def _render_add_context(
        self, outcome: Outcome
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        text = outcome.context if outcome.context is not None else outcome.message
        return self._render_context_message(outcome, text, "add-context")

    def _render_context_message(
        self, outcome: Outcome, text: str, action_name: str
    ) -> tuple[dict[str, object], dict[str, str] | None]:
        if outcome.event in _ADDITIONAL_CONTEXT_EVENTS:
            return (
                _json(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": outcome.event,
                            "additionalContext": text,
                        }
                    }
                ),
                None,
            )
        return self._degrade(outcome, action_name, text=text)
