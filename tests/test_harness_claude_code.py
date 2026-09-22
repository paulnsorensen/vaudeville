"""Tests for the Claude Code harness adapter (AC-7).

Matrix rows: allow, log, warn, block, feedback, rewrite, escalate, ask,
add-context, run, ask-on-Stop-degrades, add-context-on-Notification-degrades.

The last row uses `Notification` rather than `Stop`. The Claude Code hooks
reference (https://code.claude.com/docs/en/hooks, read 2026-09-22) lists
`Stop` under events that accept `hookSpecificOutput.additionalContext` ("Stop
and SubagentStop also accept hookSpecificOutput.additionalContext for
non-error feedback"), so add-context does not degrade on Stop. `Notification`
is listed with "None" decision control, so it is a genuine degrade case.
"""

from __future__ import annotations

import json

from vaudeville.rules import Action, ActionName
from vaudeville.server.harness import Outcome, RenderResult
from vaudeville.server.harness.claude_code import ClaudeCodeAdapter


def _stdout_json(result: RenderResult) -> dict[str, object]:
    return dict(json.loads(result["stdout"]))


def _outcome(
    action_name: ActionName,
    event: str,
    message: str = "blocked by rule",
    updated_input: dict[str, object] | None = None,
    context: str | None = None,
) -> Outcome:
    return Outcome(
        action=Action(action=action_name),
        message=message,
        rule="git-gate",
        event=event,
        updated_input=updated_input,
        context=context,
    )


class TestRenderMatrix:
    def test_allow(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("allow", "PreToolUse"))
        assert result == {"stdout": "{}", "exit_code": 0, "downgrades": []}
        assert result["downgrades"] == []

    def test_log(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("log", "PostToolUse"))
        assert result == {"stdout": "{}", "exit_code": 0, "downgrades": []}

    def test_warn(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("warn", "PostToolUse", message="careful"))
        assert result["exit_code"] == 0
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "careful"}

    def test_block(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("block", "PreToolUse", message="[git-gate] no force push")
        )
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "[git-gate] no force push",
            }
        }
        assert result["exit_code"] == 0
        assert result["downgrades"] == []

    def test_feedback(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome(
                "feedback", "PreToolUse", message="[git-gate] use --force-with-lease"
            )
        )
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "[git-gate] use --force-with-lease",
            }
        }
        assert result["downgrades"] == []

    def test_rewrite(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome(
                "rewrite",
                "PreToolUse",
                updated_input={"command": "git push --force-with-lease"},
            )
        )
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": "git push --force-with-lease"},
            }
        }
        assert result["downgrades"] == []

    def test_escalate(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("escalate", "PreToolUse"))
        assert result == {"stdout": "{}", "exit_code": 0, "downgrades": []}
        assert result["downgrades"] == []

    def test_ask(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("ask", "PreToolUse", message="confirm?"))
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "confirm?",
            }
        }
        assert result["downgrades"] == []

    def test_add_context(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("add-context", "PreToolUse", context="branch: main")
        )
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "branch: main",
            }
        }
        assert result["downgrades"] == []

    def test_run(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("run", "PostToolUse"))
        assert result == {"stdout": "{}", "exit_code": 0, "downgrades": []}
        assert result["downgrades"] == []


class TestDegrade:
    def test_ask_on_stop_degrades(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("ask", "Stop", message="confirm?"))
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "confirm?"}
        assert result["exit_code"] == 0
        assert result["downgrades"] == [{"from": "ask", "to": "warn", "event": "Stop"}]

    def test_add_context_on_notification_degrades(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("add-context", "Notification", context="branch: main")
        )
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "branch: main"}
        assert result["downgrades"] == [
            {"from": "add-context", "to": "warn", "event": "Notification"}
        ]

    def test_block_degrades_on_event_with_no_decision_channel(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("block", "Notification", message="nope"))
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "nope"}
        assert result["downgrades"] == [
            {"from": "block", "to": "warn", "event": "Notification"}
        ]

    def test_rewrite_degrades_without_tool_input_event(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("rewrite", "Stop"))
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "blocked by rule"}
        assert result["downgrades"] == [
            {"from": "rewrite", "to": "warn", "event": "Stop"}
        ]

    def test_interleaved_render_does_not_leak_downgrades(self) -> None:
        """`render` is stateless: interleaved calls don't share downgrades (H2)."""
        adapter = ClaudeCodeAdapter()
        result_a = adapter.render(_outcome("ask", "Stop", message="confirm?"))
        result_b = adapter.render(
            _outcome("add-context", "Notification", context="branch: main")
        )
        assert result_a["downgrades"] == [
            {"from": "ask", "to": "warn", "event": "Stop"}
        ]
        assert result_b["downgrades"] == [
            {"from": "add-context", "to": "warn", "event": "Notification"}
        ]


class TestNormalize:
    def test_pre_tool_use_carries_tool_fields(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = {
            "hook_event_name": "PreToolUse",
            "cwd": "/repo",
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force"},
        }
        event = adapter.normalize(raw)
        assert event.harness == "claude-code"
        assert event.event == "PreToolUse"
        assert event.tool_name == "Bash"
        assert event.tool_input == {"command": "git push --force"}
        assert event.cwd == "/repo"
        assert event.text == "git push --force"
        assert event.raw == raw

    def test_user_prompt_submit_uses_prompt_field(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/repo",
            "prompt": "do the thing",
        }
        event = adapter.normalize(raw)
        assert event.text == "do the thing"
        assert event.tool_input is None

    def test_no_recognizable_text_field_is_empty(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = {"hook_event_name": "SessionStart", "cwd": "/repo"}
        event = adapter.normalize(raw)
        assert event.text == ""

    def test_pre_tool_use_uses_tool_input_body(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = {
            "hook_event_name": "PreToolUse",
            "cwd": "/repo",
            "tool_name": "mcp__github__create_issue",
            "tool_input": {"body": "deferred: revisit auth flow later"},
        }
        event = adapter.normalize(raw)
        assert event.text == "deferred: revisit auth flow later"
