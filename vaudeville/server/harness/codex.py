"""Codex CLI adapter: renders the ten-action vocabulary as Codex hook JSON.

Output shapes follow the Codex hooks reference
(https://learn.chatgpt.com/docs/hooks, read 2026-09-26, mirrored at
`.cheese/research/codex-plugin/codex-plugin.md` and
`.cheese/research/harness-hooks/harness-hooks.md`). Codex's `hookSpecificOutput`
shape for `PreToolUse` (deny, or allow+updatedInput) and the top-level
`decision: "block"` shape are byte-identical to Claude Code's, so this module
mirrors `claude_code.py`'s structure without importing it (that file belongs
to another PR). An action that has no channel on a given event degrades to
`warn` and the downgrade is recorded in the render result's `downgrades` list,
the same convention the Claude Code adapter uses.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from vaudeville.core.protocol import GENERIC_ALLOW, HookResponse
from vaudeville.server.harness import HookEvent, Outcome, RenderResult

_Rendered = tuple[HookResponse, dict[str, str] | None]
_RenderStep = Callable[[Outcome], _Rendered]

# Codex's shell tool is named `Bash` like Claude Code's, but its file-edit
# tool is `apply_patch`; the docs note matcher values can also use `Edit` or
# `Write` for it. Rules use the Claude vocabulary, so map to `Edit`.
_TOOL_NAME_MAP: dict[str, str] = {"apply_patch": "Edit"}

# hookSpecificOutput.permissionDecision (allow/deny) and updatedInput
# (allow only) exist on PreToolUse — same shape as Claude Code's.
_PERMISSION_DECISION_EVENTS = frozenset({"PreToolUse"})

# PermissionRequest has its own decision shape: hookSpecificOutput.decision
# .behavior ("allow"/"deny"). It must not carry updatedInput.
_PERMISSION_REQUEST_EVENTS = frozenset({"PermissionRequest"})

# Top-level `decision: "block"` + `reason`: UserPromptSubmit and Stop/
# SubagentStop treat it as "continue"; PostToolUse's docs call it out as
# PostToolUse's only lever besides exit code 2 (no updatedInput there).
_TOP_LEVEL_DECISION_EVENTS = frozenset({"UserPromptSubmit", "PostToolUse", "Stop", "SubagentStop"})

# hookSpecificOutput.additionalContext is confirmed for these events.
_ADDITIONAL_CONTEXT_EVENTS = frozenset(
    {"SessionStart", "SubagentStart", "PreToolUse", "PostToolUse"}
)

_TEXT_FIELDS_BY_EVENT: dict[str, tuple[str, ...]] = {
    "UserPromptSubmit": ("prompt",),
    "Stop": ("last_assistant_message",),
    "SubagentStop": ("last_assistant_message",),
}

# `input` is apply_patch's own patch-text argument; the rest mirror Claude
# Code's tool-input text fields for other tools.
_TOOL_INPUT_TEXT_FIELDS = ("input", "patch", "command", "content", "new_string", "prompt", "body")


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


def _json(payload: dict[str, object]) -> HookResponse:
    return {"stdout": json.dumps(payload), "exit_code": 0}


def _with_additional_context(stdout: str, event: str, context: str) -> str:
    """Put merged add-context text beside a primary action's payload."""
    body = json.loads(stdout) if stdout else {}
    specific = body.setdefault("hookSpecificOutput", {"hookEventName": event})
    existing = specific.get("additionalContext")
    specific["additionalContext"] = f"{existing}\n\n{context}" if existing else context
    return json.dumps(body)


class CodexAdapter:
    """Adapter for the Codex CLI harness."""

    def normalize(self, raw: Mapping[str, object]) -> HookEvent:
        event = str(raw.get("hook_event_name", ""))
        tool_input = raw.get("tool_input")
        native_tool_name = raw.get("tool_name")
        tool_name = None
        if isinstance(native_tool_name, str):
            tool_name = _TOOL_NAME_MAP.get(native_tool_name, native_tool_name)
        return HookEvent(
            harness="codex",
            event=event,
            tool_name=tool_name,
            tool_input=tool_input if isinstance(tool_input, dict) else None,
            cwd=str(raw.get("cwd", "")),
            raw=dict(raw),
            text=_derive_text(event, raw),
        )

    def render(self, outcome: Outcome) -> RenderResult:
        name = outcome.action.action
        renderers: dict[str, _RenderStep] = {
            "allow": self._render_allow,
            "log": self._render_allow,
            "warn": self._render_warn,
            "block": self._render_block,
            "feedback": self._render_feedback,
            "rewrite": self._render_rewrite,
            "escalate": self._render_allow,
            "ask": self._render_ask,
            "add-context": self._render_add_context,
            "run": self._render_allow,
        }
        method = renderers.get(name)
        payload, downgrade = self._degrade(outcome, name) if method is None else method(outcome)
        downgrades = [downgrade] if downgrade is not None else []
        stdout = payload["stdout"]
        if outcome.context and name != "add-context":
            if outcome.event in _ADDITIONAL_CONTEXT_EVENTS:
                stdout = _with_additional_context(stdout, outcome.event, outcome.context)
            else:
                downgrades.append({"from": "add-context", "to": "dropped", "event": outcome.event})
        return {
            "stdout": stdout,
            "exit_code": payload["exit_code"],
            "downgrades": downgrades,
        }

    def _degrade(self, outcome: Outcome, action_name: str, text: str | None = None) -> _Rendered:
        downgrade = {"from": action_name, "to": "warn", "event": outcome.event}
        payload = _json({"systemMessage": text if text is not None else outcome.message})
        return payload, downgrade

    def render_allow(self) -> RenderResult:
        return {**GENERIC_ALLOW, "downgrades": []}

    def _render_allow(self, outcome: Outcome) -> _Rendered:
        del outcome
        return GENERIC_ALLOW.copy(), None

    def _render_warn(self, outcome: Outcome) -> _Rendered:
        return _json({"systemMessage": outcome.message}), None

    def _render_block(self, outcome: Outcome) -> _Rendered:
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
        if event in _PERMISSION_REQUEST_EVENTS:
            return (
                _json(
                    {
                        "systemMessage": outcome.message,
                        "hookSpecificOutput": {
                            "hookEventName": event,
                            "decision": {"behavior": "deny"},
                        },
                    }
                ),
                None,
            )
        if event in _TOP_LEVEL_DECISION_EVENTS:
            return _json({"decision": "block", "reason": outcome.message}), None
        return self._degrade(outcome, "block")

    def _render_ask(self, outcome: Outcome) -> _Rendered:
        # Codex documents no ask/prompt permission value (only allow/deny), on
        # either PreToolUse or PermissionRequest; ask always degrades.
        return self._degrade(outcome, "ask")

    def _render_rewrite(self, outcome: Outcome) -> _Rendered:
        # Codex rejects any updatedInput shape paired with a permissionDecision
        # other than "allow" ("Return updatedInput only with permissionDecision:
        # 'allow'"), unlike Claude Code's ask+updatedInput.
        if outcome.event in _PERMISSION_DECISION_EVENTS and outcome.updated_input is not None:
            return (
                _json(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": outcome.event,
                            "permissionDecision": "allow",
                            "updatedInput": outcome.updated_input,
                        }
                    }
                ),
                None,
            )
        return self._degrade(outcome, "rewrite")

    def _render_feedback(self, outcome: Outcome) -> _Rendered:
        return self._render_context_message(outcome, outcome.message, "feedback")

    def _render_add_context(self, outcome: Outcome) -> _Rendered:
        text = outcome.context if outcome.context is not None else outcome.message
        return self._render_context_message(outcome, text, "add-context")

    def _render_context_message(self, outcome: Outcome, text: str, action_name: str) -> _Rendered:
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
