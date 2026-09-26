"""Tests for the Pi / oh-my-pi harness adapter (PR C).

Covers `normalize()` (native event/tool-name mapping to the Claude Code rule
vocabulary) and `render()` (the ten-action vocabulary collapsed to the
six-action verdict `pi/extensions/vaudeville.ts` reads from stdout).
"""

from __future__ import annotations

import json

from vaudeville.rules import Action, ActionName
from vaudeville.server.harness import Outcome, RenderResult
from vaudeville.server.harness.pi import PiAdapter


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


class TestNormalize:
    def test_tool_call_maps_event_and_tool_name(self) -> None:
        event = PiAdapter().normalize(
            {
                "type": "tool_call",
                "toolName": "bash",
                "input": {"command": "git push --force"},
                "cwd": "/repo",
            }
        )
        assert event.harness == "pi"
        assert event.event == "PreToolUse"
        assert event.tool_name == "Bash"
        assert event.tool_input == {"command": "git push --force"}
        assert event.cwd == "/repo"
        assert event.text == "git push --force"

    def test_tool_result_maps_to_post_tool_use(self) -> None:
        event = PiAdapter().normalize(
            {"type": "tool_result", "toolName": "write", "input": {"content": "x"}}
        )
        assert event.event == "PostToolUse"
        assert event.tool_name == "Write"

    def test_edit_text_joins_native_edits_new_text(self) -> None:
        event = PiAdapter().normalize(
            {
                "type": "tool_call",
                "toolName": "edit",
                "input": {
                    "path": "a.py",
                    "edits": [
                        {"oldText": "x = 1", "newText": "x = 2"},
                        {"oldText": "y", "newText": ""},
                        "not-an-edit",
                        {"oldText": "z", "newText": "z = 3"},
                    ],
                },
            }
        )
        assert event.tool_name == "Edit"
        assert event.text == "x = 2\nz = 3"
        assert event.tool_input == {
            "path": "a.py",
            "edits": [
                {"oldText": "x = 1", "newText": "x = 2"},
                {"oldText": "y", "newText": ""},
                "not-an-edit",
                {"oldText": "z", "newText": "z = 3"},
            ],
        }

    def test_edit_without_edits_list_falls_back_to_result_content(self) -> None:
        event = PiAdapter().normalize(
            {
                "type": "tool_result",
                "toolName": "edit",
                "input": {"path": "a.py", "edits": "bad"},
                "content": [{"type": "text", "text": "applied"}, {"type": "image"}],
            }
        )
        assert event.text == "applied"

    def test_tool_result_reads_string_content(self) -> None:
        event = PiAdapter().normalize(
            {"type": "tool_result", "toolName": "read", "input": {"path": "a"}, "content": "body"}
        )
        assert event.text == "body"

    def test_unknown_content_shape_yields_empty_text(self) -> None:
        event = PiAdapter().normalize({"type": "tool_result", "toolName": "ls", "content": 3})
        assert event.tool_name == "ls"
        assert event.text == ""

    def test_input_event_maps_to_user_prompt_submit_and_carries_text(self) -> None:
        event = PiAdapter().normalize({"type": "input", "text": "do the thing"})
        assert event.event == "UserPromptSubmit"
        assert event.tool_name is None
        assert event.text == "do the thing"

    def test_agent_end_maps_to_stop_and_extracts_message_text(self) -> None:
        event = PiAdapter().normalize(
            {"type": "agent_end", "message": {"content": [{"text": "done"}]}}
        )
        assert event.event == "Stop"
        assert event.text == "done"

    def test_agent_before_settle_also_maps_to_stop(self) -> None:
        event = PiAdapter().normalize({"type": "agent_before_settle", "message": "done"})
        assert event.event == "Stop"
        assert event.text == "done"

    def test_session_start_maps_and_has_no_tool(self) -> None:
        event = PiAdapter().normalize({"type": "session_start", "reason": "startup"})
        assert event.event == "SessionStart"
        assert event.tool_name is None

    def test_unknown_tool_name_passes_through_unmapped(self) -> None:
        event = PiAdapter().normalize(
            {"type": "tool_call", "toolName": "custom_tool", "input": {}}
        )
        assert event.tool_name == "custom_tool"


class TestRenderMatrix:
    def test_allow(self) -> None:
        result = PiAdapter().render(_outcome("allow", "PreToolUse"))
        assert _stdout_json(result) == {"action": "allow"}
        assert result["exit_code"] == 0
        assert result["downgrades"] == []

    def test_render_allow_matches_the_render_of_allow_action(self) -> None:
        adapter = PiAdapter()
        allow = adapter.render_allow()
        assert _stdout_json(allow) == {"action": "allow"}
        assert allow["exit_code"] == 0
        assert allow["downgrades"] == []
        assert adapter.render(_outcome("allow", "Stop"))["stdout"] == allow["stdout"]

    def test_log_and_escalate_and_run_render_as_allow(self) -> None:
        adapter = PiAdapter()
        for action_name in ("log", "escalate", "run"):
            result = adapter.render(_outcome(action_name, "PostToolUse"))
            assert _stdout_json(result) == {"action": "allow"}
            assert result["downgrades"] == []

    def test_warn(self) -> None:
        result = PiAdapter().render(_outcome("warn", "PostToolUse", message="careful"))
        assert _stdout_json(result) == {"action": "warn", "message": "careful"}
        assert result["downgrades"] == []

    def test_block_on_pre_tool_use(self) -> None:
        result = PiAdapter().render(_outcome("block", "PreToolUse", message="no force push"))
        assert _stdout_json(result) == {"action": "block", "reason": "no force push"}
        assert result["downgrades"] == []

    def test_block_on_stop_becomes_continue(self) -> None:
        """Stop has no tool-block channel; `block` means force one more turn."""
        result = PiAdapter().render(_outcome("block", "Stop", message="keep going"))
        assert _stdout_json(result) == {"action": "continue", "message": "keep going"}
        assert result["downgrades"] == []

    def test_rewrite_on_pre_tool_use(self) -> None:
        result = PiAdapter().render(
            _outcome(
                "rewrite",
                "PreToolUse",
                updated_input={"command": "git push --force-with-lease"},
            )
        )
        assert _stdout_json(result) == {
            "action": "rewrite",
            "input": {"command": "git push --force-with-lease"},
        }
        assert result["downgrades"] == []

    def test_feedback(self) -> None:
        result = PiAdapter().render(
            _outcome("feedback", "PreToolUse", message="use --force-with-lease")
        )
        assert _stdout_json(result) == {"action": "context", "message": "use --force-with-lease"}
        assert result["downgrades"] == []

    def test_add_context(self) -> None:
        result = PiAdapter().render(_outcome("add-context", "PreToolUse", context="branch: main"))
        assert _stdout_json(result) == {"action": "context", "message": "branch: main"}
        assert result["downgrades"] == []


class TestDegrade:
    def test_ask_always_degrades_to_warn(self) -> None:
        result = PiAdapter().render(_outcome("ask", "PreToolUse", message="confirm?"))
        assert _stdout_json(result) == {"action": "warn", "message": "confirm?"}
        assert result["downgrades"] == [{"from": "ask", "to": "warn", "event": "PreToolUse"}]

    def test_rewrite_degrades_on_post_tool_use(self) -> None:
        result = PiAdapter().render(
            _outcome("rewrite", "PostToolUse", updated_input={"content": "x"})
        )
        assert _stdout_json(result) == {"action": "warn", "message": "blocked by rule"}
        assert result["downgrades"] == [{"from": "rewrite", "to": "warn", "event": "PostToolUse"}]

    def test_rewrite_degrades_without_updated_input(self) -> None:
        result = PiAdapter().render(_outcome("rewrite", "PreToolUse"))
        assert _stdout_json(result) == {"action": "warn", "message": "blocked by rule"}
        assert result["downgrades"] == [{"from": "rewrite", "to": "warn", "event": "PreToolUse"}]

    def test_interleaved_render_does_not_leak_downgrades(self) -> None:
        adapter = PiAdapter()
        result_a = adapter.render(_outcome("ask", "PreToolUse", message="confirm?"))
        result_b = adapter.render(_outcome("rewrite", "PostToolUse"))
        assert result_a["downgrades"] == [{"from": "ask", "to": "warn", "event": "PreToolUse"}]
        assert result_b["downgrades"] == [
            {"from": "rewrite", "to": "warn", "event": "PostToolUse"}
        ]


class TestContextBesidePrimary:
    def test_warn_carries_secondary_context(self) -> None:
        result = PiAdapter().render(
            _outcome("warn", "PreToolUse", message="careful", context="branch: main")
        )
        assert _stdout_json(result) == {
            "action": "warn",
            "message": "careful",
            "context": "branch: main",
        }
        assert result["downgrades"] == []

    def test_context_does_not_double_up_on_add_context_primary(self) -> None:
        """An add-context primary already carries its own text; no duplication."""
        result = PiAdapter().render(_outcome("add-context", "PreToolUse", context="branch: main"))
        assert _stdout_json(result) == {"action": "context", "message": "branch: main"}
