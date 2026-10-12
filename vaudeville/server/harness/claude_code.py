"""Claude Code adapter: renders the ten-action vocabulary as hook JSON.

Output shapes follow the Claude Code hooks reference
(https://code.claude.com/docs/en/hooks, read 2026-09-22). An action that has
no channel on a given event degrades to `warn` and the downgrade is recorded
in the render result's `downgrades` list for the pipeline to log (AC-7).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from vaudeville.core.protocol import GENERIC_ALLOW, HookResponse
from vaudeville.server.harness import HookEvent, Outcome, RenderResult

_Rendered = tuple[HookResponse, dict[str, str] | None]
_RenderStep = Callable[[Outcome], _Rendered]

# hookSpecificOutput.permissionDecision (allow/deny/ask) exists on
# PreToolUse and PreModelSwitch ("PreModelSwitch accepts allow, deny, and
# ask", docs read 2026-09-26).
_PERMISSION_DECISION_EVENTS = frozenset({"PreToolUse", "PreModelSwitch"})

# updatedInput on the permissionDecision channel exists only on PreToolUse;
# PreModelSwitch explicitly does not accept it.
_REWRITE_INPUT_EVENTS = frozenset({"PreToolUse"})

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

# Payload fields per the hooks reference (read 2026-09-30).
_TEXT_FIELDS_BY_EVENT: dict[str, tuple[str, ...]] = {
    "UserPromptSubmit": ("prompt",),
    "UserPromptExpansion": ("expanded_prompt",),
    "Stop": ("last_assistant_message",),
    "SubagentStop": ("last_assistant_message",),
}

_TOOL_INPUT_TEXT_FIELDS = ("command", "content", "new_string", "prompt", "body")


def _tool_input_text(tool_input: Mapping[str, object]) -> str:
    for key in _TOOL_INPUT_TEXT_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _model_switch_text(raw: Mapping[str, object]) -> str:
    from_model, to_model = raw.get("from_model"), raw.get("to_model")
    if not isinstance(to_model, str) or not to_model:
        return ""
    source = f" from {from_model}" if isinstance(from_model, str) and from_model else ""
    text = f"Switch model{source} to {to_model}"
    reason = raw.get("reason")
    return f"{text} (reason: {reason})" if isinstance(reason, str) and reason else text


def _result_content_text(content: object) -> str:
    """A tool_result `content` is a string or a list of text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _tool_batch_text(raw: Mapping[str, object]) -> str:
    """One line per call (`Name: input`), then one line per result."""
    lines: list[str] = []
    uses = raw.get("tool_uses")
    for use in uses if isinstance(uses, list) else ():
        if not isinstance(use, dict):
            continue
        name, tool_input = use.get("name"), use.get("input")
        if not isinstance(name, str) or not isinstance(tool_input, dict):
            continue
        lines.append(f"{name}: {_tool_input_text(tool_input) or json.dumps(tool_input)}")
    results = raw.get("tool_results")
    for result in results if isinstance(results, list) else ():
        text = _result_content_text(result.get("content")) if isinstance(result, dict) else ""
        if text:
            lines.append(f"Result: {text}")
    return "\n".join(lines)


_TEXT_BUILDERS_BY_EVENT: dict[str, Callable[[Mapping[str, object]], str]] = {
    "PreModelSwitch": _model_switch_text,
    "PostToolBatch": _tool_batch_text,
}


def _derive_text(event: str, raw: Mapping[str, object]) -> str:
    """Best-effort classifiable text for an event."""
    builder = _TEXT_BUILDERS_BY_EVENT.get(event)
    if builder is not None:
        return builder(raw)
    for field in _TEXT_FIELDS_BY_EVENT.get(event, ()):
        value = raw.get(field)
        if isinstance(value, str) and value:
            return value
    tool_input = raw.get("tool_input")
    if isinstance(tool_input, dict):
        text = _tool_input_text(tool_input)
        if text:
            return text
    tool_response = raw.get("tool_response")
    if isinstance(tool_response, str) and tool_response:
        return tool_response
    return ""


# Transcript markers for background subagents (Claude Code 2.1.296 transcripts,
# read 2026-10-11). A launch returns a tool result with this prefix. The
# completion names the launch tool-use id in a queue enqueue row, and then in a
# user row (turn start) or a queued-command attachment (absorbed mid-turn).
_ASYNC_LAUNCH_PREFIX = "Async agent launched successfully."
_TASK_NOTIFICATION = "<task-notification>"
_NOTIFIED_TOOL_USE_ID = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")


def _marker_rows(path: Path) -> Iterator[dict[str, object]]:
    """Rows that can carry a tool call, launch or notification; skip the rest unparsed."""
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if not any(
                marker in line
                for marker in ('"tool_use"', _ASYNC_LAUNCH_PREFIX, _TASK_NOTIFICATION)
            ):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _background_call_ids(content: object) -> Iterator[str]:
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_use_id, tool_input = block.get("id"), block.get("input")
        if (
            block.get("name") in ("Agent", "Task")
            and isinstance(tool_use_id, str)
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
        ):
            yield tool_use_id


def _launched_ids(content: object, background_calls: set[str]) -> Iterator[str]:
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id")
        text = _result_content_text(block.get("content"))
        if (
            isinstance(tool_use_id, str)
            and tool_use_id in background_calls
            and text.startswith(_ASYNC_LAUNCH_PREFIX)
        ):
            yield tool_use_id


def _notification_text(row: Mapping[str, object]) -> str:
    """The text of a row that delivers a notification, else empty.

    A tool result that quotes a notification is not a delivery.
    """
    kind, content = row.get("type"), None
    if kind == "queue-operation" and row.get("operation") == "enqueue":
        content = row.get("content")
    elif kind == "attachment":
        attachment = row.get("attachment")
        if isinstance(attachment, dict) and attachment.get("commandMode") == "task-notification":
            content = attachment.get("prompt")
    elif kind == "user":
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    text = _result_content_text(content)
    return text if _TASK_NOTIFICATION in text else ""


def _background_agents_pending(raw: Mapping[str, object]) -> bool:
    """True when a background subagent launch has no completion notification.

    An unreadable transcript counts as nothing pending, so rules still run.
    """
    raw_path = raw.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    path = Path(raw_path)
    if not path.is_file():
        return False
    launched: set[str] = set()
    background_calls: set[str] = set()
    notified: set[str] = set()
    try:
        for row in _marker_rows(path):
            message = row.get("message")
            if isinstance(message, dict):
                if row.get("type") == "assistant":
                    background_calls.update(_background_call_ids(message.get("content")))
                elif row.get("type") == "user":
                    launched.update(_launched_ids(message.get("content"), background_calls))
            notified.update(_NOTIFIED_TOOL_USE_ID.findall(_notification_text(row)))
    except OSError:
        return False
    return bool(launched - notified)


def _json(payload: dict[str, object]) -> HookResponse:
    return {"stdout": json.dumps(payload), "exit_code": 0}


def _with_additional_context(stdout: str, event: str, context: str) -> str:
    """Put merged add-context text beside a primary action's payload (AC-16)."""
    body = json.loads(stdout) if stdout else {}
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
            stop_hook_active=raw.get("stop_hook_active") is True,
            awaiting_background=event == "Stop" and _background_agents_pending(raw),
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

    # `log`'s side effect belongs to the pipeline; the hook output is allow.
    _render_log = _render_allow

    # The pipeline resolves `escalate` before render is reached; a defensive
    # fallback here renders it as allow.
    _render_escalate = _render_allow

    # `run` starts a fire-and-forget process in the effects layer; the hook
    # itself must not block.
    _render_run = _render_allow

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
        if event in _TOP_LEVEL_DECISION_EVENTS:
            return _json({"decision": "block", "reason": outcome.message}), None
        return self._degrade(outcome, "block")

    def _render_ask(self, outcome: Outcome) -> _Rendered:
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

    def _render_rewrite(self, outcome: Outcome) -> _Rendered:
        # `ask` shows the changed input for approval; a rewrite never grants
        # more permission than the unmodified call gets.
        if outcome.event in _REWRITE_INPUT_EVENTS and outcome.updated_input is not None:
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
