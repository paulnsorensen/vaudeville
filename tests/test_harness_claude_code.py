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
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from _hook_helpers import async_launch, task_notification, write_transcript

from vaudeville.core.protocol import GENERIC_ALLOW
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
        action=Action.model_construct(action=action_name),
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
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}
        assert result["downgrades"] == []

    def test_render_allow_matches_the_runner_fail_open_allow(self) -> None:
        """One allow shape on the wire: empty stdout, exit 0."""
        adapter = ClaudeCodeAdapter()
        assert adapter.render_allow() == {**GENERIC_ALLOW, "downgrades": []}
        assert adapter.render(_outcome("allow", "Stop"))["stdout"] == ""

    def test_log_with_context_puts_context_on_the_wire(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("log", "PreToolUse", context="branch: main"))
        assert _stdout_json(result) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "branch: main",
            }
        }

    def test_log(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("log", "PostToolUse"))
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}

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
            _outcome("feedback", "PreToolUse", message="[git-gate] use --force-with-lease")
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
                "permissionDecision": "ask",
                "updatedInput": {"command": "git push --force-with-lease"},
            }
        }
        assert result["downgrades"] == []

    def test_rewrite_never_emits_allow(self) -> None:
        """A rewrite changes tool input only; it must not skip the permission prompt."""
        adapter = ClaudeCodeAdapter()
        events = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
        for event in events:
            updates: tuple[dict[str, object] | None, ...] = ({"content": "x"}, None)
            for updated in updates:
                result = adapter.render(
                    _outcome("rewrite", event, message="m", updated_input=updated)
                )
                assert '"allow"' not in result["stdout"], (event, updated)

    def test_escalate(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("escalate", "PreToolUse"))
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}
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
        result = adapter.render(_outcome("add-context", "PreToolUse", context="branch: main"))
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
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}
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
        result = adapter.render(_outcome("add-context", "Notification", context="branch: main"))
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
        assert result["downgrades"] == [{"from": "block", "to": "warn", "event": "Notification"}]

    def test_rewrite_degrades_without_tool_input_event(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("rewrite", "Stop"))
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "blocked by rule"}
        assert result["downgrades"] == [{"from": "rewrite", "to": "warn", "event": "Stop"}]

    def test_interleaved_render_does_not_leak_downgrades(self) -> None:
        """`render` is stateless: interleaved calls don't share downgrades (H2)."""
        adapter = ClaudeCodeAdapter()
        result_a = adapter.render(_outcome("ask", "Stop", message="confirm?"))
        result_b = adapter.render(_outcome("add-context", "Notification", context="branch: main"))
        assert result_a["downgrades"] == [{"from": "ask", "to": "warn", "event": "Stop"}]
        assert result_b["downgrades"] == [
            {"from": "add-context", "to": "warn", "event": "Notification"}
        ]


class TestContextBesidePrimary:
    """F15: add-context text survives a non-add-context primary (AC-16)."""

    def test_warn_carries_context_in_additional_context(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("warn", "PreToolUse", message="careful", context="branch: main")
        )
        assert _stdout_json(result) == {
            "systemMessage": "careful",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "branch: main",
            },
        }
        assert result["downgrades"] == []

    def test_feedback_appends_context_after_its_own_text(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("feedback", "Stop", message="fix it", context="branch: main")
        )
        payload = _stdout_json(result)
        assert payload["hookSpecificOutput"] == {
            "hookEventName": "Stop",
            "additionalContext": "fix it\n\nbranch: main",
        }
        assert result["downgrades"] == []

    def test_event_without_context_channel_reports_dropped_context(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("warn", "Notification", message="careful", context="ctx"))
        assert _stdout_json(result) == {"systemMessage": "careful"}
        assert result["downgrades"] == [
            {"from": "add-context", "to": "dropped", "event": "Notification"}
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

    # Payloads below follow the examples in the Claude Code hooks reference
    # (https://code.claude.com/docs/en/hooks, read 2026-09-30).

    def test_user_prompt_expansion_uses_expanded_prompt(self) -> None:
        raw = {
            "hook_event_name": "UserPromptExpansion",
            "cwd": "/repo",
            "command_name": "lint-check",
            "command_input": "src/",
            "expanded_prompt": "Run the linter on src/ directory and report issues",
        }
        event = ClaudeCodeAdapter().normalize(raw)
        assert event.text == "Run the linter on src/ directory and report issues"

    def test_pre_model_switch_describes_the_switch(self) -> None:
        raw = {
            "hook_event_name": "PreModelSwitch",
            "cwd": "/repo",
            "from_model": "claude-opus-5",
            "to_model": "claude-haiku-3-5",
            "reason": "user_requested",
        }
        event = ClaudeCodeAdapter().normalize(raw)
        assert event.text == (
            "Switch model from claude-opus-5 to claude-haiku-3-5 (reason: user_requested)"
        )

    def test_pre_model_switch_without_reason(self) -> None:
        raw = {
            "hook_event_name": "PreModelSwitch",
            "from_model": "claude-opus-5",
            "to_model": "claude-sonnet-5",
        }
        event = ClaudeCodeAdapter().normalize(raw)
        assert event.text == "Switch model from claude-opus-5 to claude-sonnet-5"

    def test_pre_model_switch_without_source_or_target(self) -> None:
        adapter = ClaudeCodeAdapter()
        to_only = adapter.normalize({"hook_event_name": "PreModelSwitch", "to_model": "m"})
        no_target = adapter.normalize({"hook_event_name": "PreModelSwitch", "from_model": "m"})
        assert to_only.text == "Switch model to m"
        assert no_target.text == ""

    def test_post_tool_batch_lists_each_call_and_result(self) -> None:
        raw = {
            "hook_event_name": "PostToolBatch",
            "cwd": "/repo",
            "tool_results": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "File edited successfully",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "content": [{"type": "text", "text": "Build passed"}],
                },
            ],
            "tool_uses": [
                {
                    "id": "toolu_1",
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "src/app.ts"},
                },
                {
                    "id": "toolu_2",
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "npm test"},
                },
            ],
        }
        event = ClaudeCodeAdapter().normalize(raw)
        assert event.text == (
            'Edit: {"file_path": "src/app.ts"}\n'
            "Bash: npm test\n"
            "Result: File edited successfully\n"
            "Result: Build passed"
        )

    def test_post_tool_batch_skips_malformed_entries(self) -> None:
        raw = {
            "hook_event_name": "PostToolBatch",
            "tool_uses": ["junk", {"name": 3, "input": None}],
            "tool_results": "not-a-list",
        }
        event = ClaudeCodeAdapter().normalize(raw)
        assert event.text == ""

    def test_post_tool_batch_skips_results_without_text(self) -> None:
        raw = {
            "hook_event_name": "PostToolBatch",
            "tool_results": ["junk", {"content": None}, {"content": [{"type": "image"}]}],
        }
        event = ClaudeCodeAdapter().normalize(raw)
        assert event.text == ""


class TestNewlyWiredEvents:
    """PreModelSwitch, PostToolBatch, UserPromptExpansion (AC-7 follow-up)."""

    def test_pre_model_switch_block_uses_permission_decision(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("block", "PreModelSwitch", message="no downgrades"))
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreModelSwitch",
                "permissionDecision": "deny",
                "permissionDecisionReason": "no downgrades",
            }
        }
        assert result["downgrades"] == []

    def test_pre_model_switch_ask(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("ask", "PreModelSwitch", message="confirm switch?"))
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PreModelSwitch",
                "permissionDecision": "ask",
                "permissionDecisionReason": "confirm switch?",
            }
        }
        assert result["downgrades"] == []

    def test_pre_model_switch_rewrite_degrades_updated_input_unsupported(self) -> None:
        """PreModelSwitch does not accept updatedInput; rewrite must degrade."""
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("rewrite", "PreModelSwitch", updated_input={"to_model": "claude-opus-5"})
        )
        payload = _stdout_json(result)
        assert payload == {"systemMessage": "blocked by rule"}
        assert result["downgrades"] == [
            {"from": "rewrite", "to": "warn", "event": "PreModelSwitch"}
        ]

    def test_post_tool_batch_add_context(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("add-context", "PostToolBatch", context="batch note"))
        payload = _stdout_json(result)
        assert payload == {
            "hookSpecificOutput": {
                "hookEventName": "PostToolBatch",
                "additionalContext": "batch note",
            }
        }
        assert result["downgrades"] == []

    def test_post_tool_batch_block_uses_top_level_decision(self) -> None:
        """PostToolBatch is in the Decision control "Top-level decision" row."""
        adapter = ClaudeCodeAdapter()
        result = adapter.render(_outcome("block", "PostToolBatch", message="nope"))
        payload = _stdout_json(result)
        assert payload == {"decision": "block", "reason": "nope"}
        assert result["downgrades"] == []

    def test_user_prompt_expansion_block_uses_top_level_decision(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.render(
            _outcome("block", "UserPromptExpansion", message="expansion blocked")
        )
        payload = _stdout_json(result)
        assert payload == {"decision": "block", "reason": "expansion blocked"}
        assert result["downgrades"] == []


def _notice_text(tool_use_id: str) -> str:
    message = task_notification(tool_use_id)["message"]
    assert isinstance(message, dict)
    return str(message["content"])


def _stop(transcript: str, event: str = "Stop") -> dict[str, object]:
    return {"hook_event_name": event, "cwd": "/repo", "transcript_path": transcript}


def _awaiting(tmp_path: Path, rows: Sequence[Mapping[str, object]], event: str = "Stop") -> bool:
    transcript = write_transcript(tmp_path / "session.jsonl", rows)
    return ClaudeCodeAdapter().normalize(_stop(transcript, event)).awaiting_background


class TestAwaitingBackground:
    def test_launch_without_notification_is_awaiting(self, tmp_path: Path) -> None:
        assert _awaiting(tmp_path, [async_launch("toolu_a")]) is True

    def test_notified_launch_is_not_awaiting(self, tmp_path: Path) -> None:
        rows = [async_launch("toolu_a"), task_notification("toolu_a")]
        assert _awaiting(tmp_path, rows) is False

    def test_enqueued_notification_completes_the_launch(self, tmp_path: Path) -> None:
        text = _notice_text("toolu_a")
        enqueue = {"type": "queue-operation", "operation": "enqueue", "content": text}
        assert _awaiting(tmp_path, [async_launch("toolu_a"), enqueue]) is False

    def test_mid_turn_queued_command_completes_the_launch(self, tmp_path: Path) -> None:
        attachment = {
            "type": "queued_command",
            "commandMode": "task-notification",
            "prompt": _notice_text("toolu_a"),
        }
        rows: list[Mapping[str, object]] = [
            async_launch("toolu_a"),
            {"type": "attachment", "attachment": attachment},
        ]
        assert _awaiting(tmp_path, rows) is False

    def test_other_queue_and_attachment_rows_do_not_complete(self, tmp_path: Path) -> None:
        text = _notice_text("toolu_a")
        rows: list[Mapping[str, object]] = [
            async_launch("toolu_a"),
            {"type": "queue-operation", "operation": "remove", "content": text},
            {"type": "attachment", "attachment": {"type": "prompt_snapshot", "prompt": text}},
        ]
        assert _awaiting(tmp_path, rows) is True

    def test_one_of_two_launches_notified_is_awaiting(self, tmp_path: Path) -> None:
        rows = [async_launch("toolu_a"), async_launch("toolu_b"), task_notification("toolu_a")]
        assert _awaiting(tmp_path, rows) is True

    def test_foreground_agent_result_is_not_awaiting(self, tmp_path: Path) -> None:
        block = {"type": "tool_result", "tool_use_id": "toolu_a", "content": "Report: done."}
        rows = [{"type": "user", "message": {"role": "user", "content": [block]}}]
        assert _awaiting(tmp_path, rows) is False

    def test_notification_quoted_in_a_tool_result_does_not_complete(self, tmp_path: Path) -> None:
        """A tool that reads another transcript must not mark a launch complete."""
        quoted = json.dumps(task_notification("toolu_a"))
        block = {"type": "tool_result", "tool_use_id": "toolu_r", "content": quoted}
        rows: list[Mapping[str, object]] = [
            async_launch("toolu_a"),
            {"type": "user", "message": {"role": "user", "content": [block]}},
        ]
        assert _awaiting(tmp_path, rows) is True

    def test_assistant_text_with_markers_is_ignored(self, tmp_path: Path) -> None:
        text = "Async agent launched successfully. <task-notification>"
        rows = [{"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}]
        assert _awaiting(tmp_path, rows) is False

    def test_malformed_marker_line_is_skipped(self, tmp_path: Path) -> None:
        transcript = tmp_path / "session.jsonl"
        launch = json.dumps(async_launch("toolu_a"))
        transcript.write_text(f"{{not json <task-notification>\n{launch}\n")
        event = ClaudeCodeAdapter().normalize(_stop(str(transcript)))
        assert event.awaiting_background is True

    def test_only_stop_checks_the_transcript(self, tmp_path: Path) -> None:
        assert _awaiting(tmp_path, [async_launch("toolu_a")], event="SubagentStop") is False

    def test_missing_transcript_is_not_awaiting(self, tmp_path: Path) -> None:
        event = ClaudeCodeAdapter().normalize(_stop(str(tmp_path / "absent.jsonl")))
        assert event.awaiting_background is False

    def test_no_transcript_path_is_not_awaiting(self) -> None:
        event = ClaudeCodeAdapter().normalize({"hook_event_name": "Stop", "cwd": "/repo"})
        assert event.awaiting_background is False

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads files without permission")
    def test_unreadable_transcript_is_not_awaiting(self, tmp_path: Path) -> None:
        transcript = Path(write_transcript(tmp_path / "session.jsonl", [async_launch("toolu_a")]))
        transcript.chmod(0)
        try:
            event = ClaudeCodeAdapter().normalize(_stop(str(transcript)))
        finally:
            transcript.chmod(0o600)
        assert event.awaiting_background is False
