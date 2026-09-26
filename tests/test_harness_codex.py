"""Tests for the Codex CLI harness adapter (PR B, harness-plugins mini-spec).

Matrix rows: allow, log, warn, block, feedback, rewrite, escalate, ask,
add-context, run, plus Codex-specific degrade cases (ask has no channel on
any event; rewrite only has a channel on PreToolUse; block has three
channels: PreToolUse permissionDecision, PermissionRequest decision.behavior,
and the shared top-level decision:"block" events).
"""

from __future__ import annotations

import json

from vaudeville.core.protocol import GENERIC_ALLOW
from vaudeville.rules import Action, ActionName
from vaudeville.server.harness import Outcome, RenderResult
from vaudeville.server.harness.codex import CodexAdapter


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
        adapter = CodexAdapter()
        result = adapter.render(_outcome("allow", "PreToolUse"))
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}

    def test_render_allow_matches_the_runner_fail_open_allow(self) -> None:
        adapter = CodexAdapter()
        assert adapter.render_allow() == {**GENERIC_ALLOW, "downgrades": []}
        assert adapter.render(_outcome("allow", "Stop"))["stdout"] == ""

    def test_log(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("log", "PostToolUse"))
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}

    def test_warn(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("warn", "PostToolUse", message="careful"))
        assert result["exit_code"] == 0
        assert _stdout_json(result) == {"systemMessage": "careful"}
        assert result["downgrades"] == []

    def test_block_on_pre_tool_use(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(
            _outcome("block", "PreToolUse", message="[git-gate] no force push")
        )
        assert _stdout_json(result) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "[git-gate] no force push",
            }
        }
        assert result["downgrades"] == []

    def test_block_on_permission_request(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("block", "PermissionRequest", message="denied"))
        assert _stdout_json(result) == {
            "systemMessage": "denied",
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny"},
            },
        }
        assert result["downgrades"] == []

    def test_block_on_top_level_decision_event(self) -> None:
        adapter = CodexAdapter()
        for event in ("UserPromptSubmit", "PostToolUse", "Stop", "SubagentStop"):
            result = adapter.render(_outcome("block", event, message="nope"))
            assert _stdout_json(result) == {"decision": "block", "reason": "nope"}, event
            assert result["downgrades"] == [], event

    def test_feedback(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(
            _outcome("feedback", "PreToolUse", message="[git-gate] use --force-with-lease")
        )
        assert _stdout_json(result) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "[git-gate] use --force-with-lease",
            }
        }
        assert result["downgrades"] == []

    def test_rewrite_on_pre_tool_use(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(
            _outcome(
                "rewrite",
                "PreToolUse",
                updated_input={"command": "git push --force-with-lease"},
            )
        )
        assert _stdout_json(result) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": "git push --force-with-lease"},
            }
        }
        assert result["downgrades"] == []

    def test_rewrite_degrades_on_post_tool_use(self) -> None:
        """Codex docs: PostToolUse does not support rewriting tool input."""
        adapter = CodexAdapter()
        result = adapter.render(_outcome("rewrite", "PostToolUse", updated_input={"command": "x"}))
        assert _stdout_json(result) == {"systemMessage": "blocked by rule"}
        assert result["downgrades"] == [{"from": "rewrite", "to": "warn", "event": "PostToolUse"}]

    def test_rewrite_degrades_without_updated_input(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("rewrite", "PreToolUse"))
        assert result["downgrades"] == [{"from": "rewrite", "to": "warn", "event": "PreToolUse"}]

    def test_escalate(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("escalate", "PreToolUse"))
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}

    def test_ask_always_degrades(self) -> None:
        """Codex documents allow/deny only, no ask/prompt permission value."""
        adapter = CodexAdapter()
        for event in ("PreToolUse", "PermissionRequest", "Stop"):
            result = adapter.render(_outcome("ask", event, message="confirm?"))
            assert _stdout_json(result) == {"systemMessage": "confirm?"}, event
            assert result["downgrades"] == [{"from": "ask", "to": "warn", "event": event}], event

    def test_add_context(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("add-context", "PreToolUse", context="branch: main"))
        assert _stdout_json(result) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "branch: main",
            }
        }
        assert result["downgrades"] == []

    def test_run(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("run", "PostToolUse"))
        assert result == {"stdout": "", "exit_code": 0, "downgrades": []}


class TestDegrade:
    def test_block_degrades_on_event_with_no_decision_channel(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("block", "SubagentStart", message="nope"))
        assert _stdout_json(result) == {"systemMessage": "nope"}
        assert result["downgrades"] == [{"from": "block", "to": "warn", "event": "SubagentStart"}]

    def test_add_context_on_interrupt_degrades(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("add-context", "Interrupt", context="branch: main"))
        assert _stdout_json(result) == {"systemMessage": "branch: main"}
        assert result["downgrades"] == [
            {"from": "add-context", "to": "warn", "event": "Interrupt"}
        ]

    def test_event_without_context_channel_reports_dropped_context(self) -> None:
        adapter = CodexAdapter()
        result = adapter.render(_outcome("warn", "Interrupt", message="careful", context="ctx"))
        assert _stdout_json(result) == {"systemMessage": "careful"}
        assert result["downgrades"] == [
            {"from": "add-context", "to": "dropped", "event": "Interrupt"}
        ]


class TestNormalize:
    def test_pre_tool_use_carries_tool_fields(self) -> None:
        adapter = CodexAdapter()
        raw = {
            "hook_event_name": "PreToolUse",
            "cwd": "/repo",
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force"},
        }
        event = adapter.normalize(raw)
        assert event.harness == "codex"
        assert event.event == "PreToolUse"
        assert event.tool_name == "Bash"
        assert event.tool_input == {"command": "git push --force"}
        assert event.cwd == "/repo"
        assert event.text == "git push --force"
        assert event.raw == raw

    def test_apply_patch_maps_to_edit(self) -> None:
        adapter = CodexAdapter()
        raw = {
            "hook_event_name": "PreToolUse",
            "cwd": "/repo",
            "tool_name": "apply_patch",
            "tool_input": {"input": "*** Begin Patch\n*** End Patch"},
        }
        event = adapter.normalize(raw)
        assert event.tool_name == "Edit"
        assert event.text == "*** Begin Patch\n*** End Patch"

    def test_user_prompt_submit_uses_prompt_field(self) -> None:
        adapter = CodexAdapter()
        raw = {"hook_event_name": "UserPromptSubmit", "cwd": "/repo", "prompt": "do the thing"}
        event = adapter.normalize(raw)
        assert event.text == "do the thing"
        assert event.tool_input is None

    def test_no_recognizable_text_field_is_empty(self) -> None:
        adapter = CodexAdapter()
        raw = {"hook_event_name": "SessionStart", "cwd": "/repo"}
        event = adapter.normalize(raw)
        assert event.text == ""
        assert event.tool_name is None
