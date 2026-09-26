"""Pi / oh-my-pi adapter: renders the ten-action vocabulary as one verdict.

Pi and oh-my-pi share the same in-process TypeScript extension runtime
(evidence: `.cheese/research/pi-omp-plugin/pi-omp-plugin.md`,
`.cheese/research/pi-hooks/pi-hooks.md`, both read 2026-09-26). The
`pi/extensions/vaudeville.ts` shim talks to this daemon over the socket and
reads one JSON verdict from `stdout`: `{action, reason?, input?, message?}`
where `action` is one of `allow`, `block`, `rewrite`, `continue`, `context`,
`warn`. The shim, not this adapter, decides how each native event channel
(`tool_call`, `tool_result`, `input`, `agent_before_settle`/`agent_end`,
`session_start`) applies that verdict, so this adapter maps the wider
Claude-vocabulary action set down to the six-action verdict and records a
downgrade (AC-7) whenever a channel cannot represent an action.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from vaudeville.core.protocol import HookResponse
from vaudeville.server.harness import HookEvent, Outcome, RenderResult

_Rendered = tuple[HookResponse, dict[str, str] | None]
_RenderStep = Callable[[Outcome], _Rendered]

# Native event name -> Claude Code rule vocabulary (`rule.event`), per the
# shared contract in `.cheese/specs/harness-plugins.md`.
_EVENT_MAP: dict[str, str] = {
    "tool_call": "PreToolUse",
    "tool_result": "PostToolUse",
    "input": "UserPromptSubmit",
    "agent_before_settle": "Stop",
    "agent_end": "Stop",
    "session_start": "SessionStart",
}

# Native built-in tool names -> Claude Code tool vocabulary (`rule.matcher`).
_TOOL_NAME_MAP: dict[str, str] = {
    "bash": "Bash",
    "edit": "Edit",
    "write": "Write",
    "read": "Read",
}

# Field names checked, in order, for classifiable text on a tool call/result.
# Pi's built-in schemas (earendil-works/pi `src/core/tools/*.ts`): bash
# `{command}`, write `{path, content}`, edit `{path, edits: [{oldText,
# newText}]}`. `tool_input` stays native, so rewrite rules name native fields.
_TOOL_INPUT_TEXT_FIELDS = ("command", "content", "prompt", "body")

# `block` on a Stop-mapped event means "force one more turn", not "block a
# tool call" — Pi/omp have no tool-block channel at the end of a run, only
# a continuation channel (`agent_before_settle` `{continue: true}` in Pi, a
# triggered follow-up message after `agent_end` in oh-my-pi).
_CONTINUE_EVENTS = frozenset({"Stop"})

# `event.input` (tool_call) is the only channel the shim can mutate/replace;
# `tool_result` cannot rewrite, and neither can input or the Stop events.
_REWRITE_EVENTS = frozenset({"PreToolUse"})

_ALLOW: HookResponse = {"stdout": json.dumps({"action": "allow"}), "exit_code": 0}


def _content_text(content: object) -> str:
    """Text from a string or a list of `{type, text}` content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text") for part in content if isinstance(part, dict)]
        return "\n".join(part for part in parts if isinstance(part, str) and part)
    return ""


def _message_text(message: object) -> str:
    """Best-effort text from a `agent_before_settle`/`agent_end` message."""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return _content_text(message.get("content"))
    return ""


def _edits_text(edits: object) -> str:
    """Joined `newText` of a Pi `edit` tool's `edits` list."""
    if not isinstance(edits, list):
        return ""
    texts = [edit.get("newText") for edit in edits if isinstance(edit, dict)]
    return "\n".join(text for text in texts if isinstance(text, str) and text)


def _derive_text(native_event: str, raw: Mapping[str, object]) -> str:
    """Best-effort classifiable text for a native Pi/omp event."""
    if native_event == "input":
        text = raw.get("text")
        return text if isinstance(text, str) else ""
    if native_event in ("agent_before_settle", "agent_end"):
        return _message_text(raw.get("message"))
    tool_input = raw.get("input")
    if isinstance(tool_input, dict):
        for key in _TOOL_INPUT_TEXT_FIELDS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
        edits = _edits_text(tool_input.get("edits"))
        if edits:
            return edits
    return _content_text(raw.get("content"))


def _json(payload: dict[str, object]) -> HookResponse:
    return {"stdout": json.dumps(payload), "exit_code": 0}


class PiAdapter:
    """Adapter for the Pi and oh-my-pi harnesses (shared extension shim)."""

    def normalize(self, raw: Mapping[str, object]) -> HookEvent:
        native_event = str(raw.get("type", ""))
        event = _EVENT_MAP.get(native_event, native_event)
        native_tool = raw.get("toolName")
        tool_name = (
            _TOOL_NAME_MAP.get(native_tool, native_tool) if isinstance(native_tool, str) else None
        )
        tool_input = raw.get("input")
        return HookEvent(
            harness="pi",
            event=event,
            tool_name=tool_name,
            tool_input=tool_input if isinstance(tool_input, dict) else None,
            cwd=str(raw.get("cwd", "")),
            raw=dict(raw),
            text=_derive_text(native_event, raw),
        )

    def render(self, outcome: Outcome) -> RenderResult:
        name = outcome.action.action
        renderers: dict[str, _RenderStep] = {
            "allow": self._render_allow,
            "log": self._render_allow,
            "escalate": self._render_allow,
            "run": self._render_allow,
            "warn": self._render_warn,
            "block": self._render_block,
            "rewrite": self._render_rewrite,
            "feedback": self._render_feedback,
            "add-context": self._render_add_context,
            "ask": self._render_ask,
        }
        method = renderers[name]
        payload, downgrade = method(outcome)
        downgrades = [downgrade] if downgrade is not None else []
        stdout = payload["stdout"]
        if outcome.context and name != "add-context":
            stdout = self._with_context(stdout, outcome.context)
        return {"stdout": stdout, "exit_code": payload["exit_code"], "downgrades": downgrades}

    def _with_context(self, stdout: str, context: str) -> str:
        """Merge a secondary rule's context text into the primary verdict.

        The verdict's `context` field is channel-agnostic; the shim decides
        per native event whether/how to surface it (AC-16 equivalent).
        """
        body = json.loads(stdout) if stdout else {}
        existing = body.get("context")
        body["context"] = f"{existing}\n\n{context}" if existing else context
        return json.dumps(body)

    def _degrade(self, outcome: Outcome, action_name: str, text: str | None = None) -> _Rendered:
        downgrade = {"from": action_name, "to": "warn", "event": outcome.event}
        payload = _json(
            {"action": "warn", "message": text if text is not None else outcome.message}
        )
        return payload, downgrade

    def render_allow(self) -> RenderResult:
        return {"stdout": _ALLOW["stdout"], "exit_code": _ALLOW["exit_code"], "downgrades": []}

    def _render_allow(self, outcome: Outcome) -> _Rendered:
        del outcome
        return {"stdout": _ALLOW["stdout"], "exit_code": _ALLOW["exit_code"]}, None

    def _render_warn(self, outcome: Outcome) -> _Rendered:
        return _json({"action": "warn", "message": outcome.message}), None

    def _render_block(self, outcome: Outcome) -> _Rendered:
        if outcome.event in _CONTINUE_EVENTS:
            return _json({"action": "continue", "message": outcome.message}), None
        return _json({"action": "block", "reason": outcome.message}), None

    def _render_rewrite(self, outcome: Outcome) -> _Rendered:
        if outcome.event in _REWRITE_EVENTS and outcome.updated_input is not None:
            return _json({"action": "rewrite", "input": outcome.updated_input}), None
        return self._degrade(outcome, "rewrite")

    def _render_feedback(self, outcome: Outcome) -> _Rendered:
        return _json({"action": "context", "message": outcome.message}), None

    def _render_add_context(self, outcome: Outcome) -> _Rendered:
        text = outcome.context if outcome.context is not None else outcome.message
        return _json({"action": "context", "message": text}), None

    def _render_ask(self, outcome: Outcome) -> _Rendered:
        return self._degrade(outcome, "ask")
