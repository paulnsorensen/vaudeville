"""Trace the experimental verification obligation through the real hook pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _hook_helpers import CONFIG, decide_fn, make_request, write_rule

from vaudeville.server.harness import HookEvent
from vaudeville.server.hook import pipeline
from vaudeville.server.hook.obligations import ObligationTracker
from vaudeville.server.user_config import PrototypeVerificationConfig, UserConfig

MESSAGE = (
    "Run a verification command after the latest edit. "
    "Use one of these exact commands: `pytest -q`"
)


@pytest.fixture(autouse=True)
def fresh_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "_obligations", ObligationTracker())


def hook(
    root: Path, event: str, tool: str = "", identity: str = "", **fields: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": event,
        "session_id": "session-a",
        "cwd": str(root),
        "tool_name": tool,
        "tool_use_id": identity,
        **fields,
    }
    return {
        "op": "hook",
        "harness": "claude-code",
        "event": event,
        "cwd": str(root),
        "payload": payload,
    }


def run(root: Path, request: dict[str, object], *, tier: str = "block") -> dict[str, object]:
    config = UserConfig.model_validate(
        {"prototype_verification": {"commands": ["pytest -q"], "tier": tier}}
    )
    return pipeline.handle_hook_request(request, config=config)


def decision(result: dict[str, object]) -> str:
    assert result["exit_code"] == 0
    stdout = result["stdout"]
    assert isinstance(stdout, str)
    if not stdout:
        return "allow"
    body = json.loads(stdout)
    if body == {"decision": "block", "reason": MESSAGE}:
        return "block"
    if body == {"systemMessage": MESSAGE}:
        return "warn"
    pytest.fail(f"unexpected hook reply: {body!r}")


def test_edit_verify_edit_trace(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    edit = hook(
        tmp_path, "PostToolUse", "Edit", "e1", tool_input={"file_path": str(tmp_path / "a.py")}
    )
    assert decision(run(tmp_path, edit)) == "allow"
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"
    command = {"command": "pytest -q"}
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b1", tool_input=command))
    run(
        tmp_path,
        hook(
            tmp_path,
            "PostToolUse",
            "Bash",
            "b1",
            tool_input=command,
            tool_response={"stdout": "ok", "stderr": "", "interrupted": False},
        ),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "allow"
    assert decision(run(tmp_path, edit)) == "allow"  # A duplicate cannot re-arm.
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "allow"
    run(
        tmp_path,
        hook(
            tmp_path,
            "PostToolUse",
            "Write",
            "e2",
            tool_input={"file_path": str(tmp_path / "b.py")},
        ),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"
    run(
        tmp_path,
        hook(
            tmp_path,
            "PostToolUse",
            "Bash",
            "b1",
            tool_input=command,
            tool_response={"stdout": "ok", "stderr": "", "interrupted": False},
        ),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"


def test_stale_and_failed_evidence(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    command = {"command": "pytest -q"}
    run(
        tmp_path,
        hook(
            tmp_path, "PreToolUse", "Edit", "e0", tool_input={"file_path": str(tmp_path / "a.py")}
        ),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "allow"
    run(
        tmp_path,
        hook(
            tmp_path, "PostToolUse", "Edit", "e1", tool_input={"file_path": str(tmp_path / "a.py")}
        ),
    )
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b1", tool_input=command))
    run(
        tmp_path,
        hook(
            tmp_path, "PostToolUse", "Edit", "e2", tool_input={"file_path": str(tmp_path / "a.py")}
        ),
    )
    run(
        tmp_path,
        hook(
            tmp_path,
            "PostToolUse",
            "Bash",
            "b1",
            tool_input=command,
            tool_response={"stdout": "ok", "stderr": "", "interrupted": False},
        ),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b2", tool_input=command))
    run(
        tmp_path,
        hook(tmp_path, "PostToolUse", "Bash", "b2", tool_input=command, tool_response="unknown"),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"


@pytest.mark.parametrize(
    "response",
    [
        {"stdout": "ok", "stderr": "", "interrupted": True},
        {"stdout": "ok", "stderr": "", "interrupted": False, "exit_code": 1},
        {"stdout": "ok", "stderr": "", "interrupted": False, "exit_code": True},
        {"stdout": "ok", "stderr": "", "interrupted": False, "exit_code": "0"},
        {"stdout": "ok", "interrupted": False},
    ],
)
def test_failed_or_unknown_bash_does_not_clear(tmp_path: Path, response: object) -> None:
    (tmp_path / ".git").mkdir()
    run(
        tmp_path,
        hook(
            tmp_path, "PostToolUse", "Edit", "e1", tool_input={"file_path": str(tmp_path / "a.py")}
        ),
    )
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b1", tool_input={"command": "pytest -q"}))
    run(
        tmp_path,
        hook(
            tmp_path,
            "PostToolUse",
            "Bash",
            "b1",
            tool_input={"command": "pytest -q"},
            tool_response=response,
        ),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"


def test_isolation_and_stop_guards(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / ".git").mkdir()
    (second / ".git").mkdir()
    run(
        first,
        hook(first, "PostToolUse", "Edit", "e1", tool_input={"file_path": str(first / "a.py")}),
    )
    other_session = hook(first, "Stop", session_id="session-b")
    assert decision(run(first, other_session)) == "allow"
    assert decision(run(second, hook(second, "Stop"))) == "allow"
    assert decision(run(first, hook(first, "Stop"))) == "block"
    assert decision(run(first, hook(first, "Stop", stop_hook_active=True))) == "warn"
    assert decision(run(first, hook(first, "Stop"))) == "block"
    run(first, hook(first, "SessionEnd"))
    assert decision(run(first, hook(first, "Stop"))) == "allow"
    run(
        first,
        hook(first, "PostToolUse", "Edit", "e2", tool_input={"file_path": str(first / "a.py")}),
    )
    assert decision(run(first, hook(first, "Stop"))) == "block"
    pipeline.handle_hook_request(hook(first, "SessionEnd"), config=UserConfig())
    assert decision(run(first, hook(first, "Stop"))) == "allow"


def test_unsupported_edits_and_default_tier(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    outside = tmp_path.parent / "outside.py"
    run(
        tmp_path,
        hook(tmp_path, "PostToolUse", "Edit", "e1", tool_input={"file_path": str(outside)}),
    )
    run(
        tmp_path,
        hook(
            tmp_path,
            "PostToolUseFailure",
            "Edit",
            "e2",
            tool_input={"file_path": str(tmp_path / "a.py")},
        ),
    )
    subagent = hook(
        tmp_path,
        "PostToolUse",
        "Edit",
        "e3",
        tool_input={"file_path": str(tmp_path / "a.py")},
        agent_id="agent",
    )
    run(tmp_path, subagent)
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "allow"
    run(
        tmp_path,
        hook(
            tmp_path, "PostToolUse", "Edit", "e4", tool_input={"file_path": str(tmp_path / "a.py")}
        ),
    )
    config = UserConfig.model_validate({"prototype_verification": {"commands": ["pytest -q"]}})
    assert config.prototype_verification is not None
    assert config.prototype_verification.tier == "warn"
    assert decision(pipeline.handle_hook_request(hook(tmp_path, "Stop"), config=config)) == "warn"
    assert (
        decision(pipeline.handle_hook_request(hook(tmp_path, "Stop"), config=UserConfig()))
        == "allow"
    )


@pytest.mark.parametrize(
    "completion",
    [
        {"hook_event_name": "PostToolUseFailure"},
        {"run_in_background": True},
        {"tool_input": {"command": "pytest -q", "run_in_background": True}},
        {"tool_input": {"command": "pytest -q && echo ok"}},
        {"tool_response": {"stdout": "ok", "stderr": "", "interrupted": False, "success": False}},
        {"tool_response": {"stdout": "ok", "stderr": "", "interrupted": False, "error": "failed"}},
        {
            "tool_response": {
                "stdout": "ok",
                "stderr": "",
                "interrupted": False,
                "background": True,
            }
        },
    ],
)
def test_invalid_completion_consumes_attempt(
    tmp_path: Path, completion: dict[str, object]
) -> None:
    (tmp_path / ".git").mkdir()
    edit = {"file_path": str(tmp_path / "a.py")}
    command = {"command": "pytest -q"}
    response = {"stdout": "ok", "stderr": "", "interrupted": False}
    run(tmp_path, hook(tmp_path, "PostToolUse", "Edit", "e1", tool_input=edit))
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b1", tool_input=command))
    event = str(completion.get("hook_event_name", "PostToolUse"))
    fields: dict[str, object] = {"tool_input": command, "tool_response": response, **completion}
    fields.pop("hook_event_name", None)
    run(tmp_path, hook(tmp_path, event, "Bash", "b1", **fields))
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"
    run(
        tmp_path,
        hook(tmp_path, "PostToolUse", "Bash", "b1", tool_input=command, tool_response=response),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"


def test_subdirectory_completion_and_removed_command_do_not_clear(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    child = tmp_path / "child"
    child.mkdir()
    edit = {"file_path": str(tmp_path / "a.py")}
    command = {"command": "pytest -q"}
    response = {"stdout": "ok", "stderr": "", "interrupted": False}
    run(tmp_path, hook(tmp_path, "PostToolUse", "Edit", "e1", tool_input=edit))
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b1", tool_input=command))
    run(
        tmp_path,
        hook(child, "PostToolUse", "Bash", "b1", tool_input=command, tool_response=response),
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"
    run(tmp_path, hook(tmp_path, "PreToolUse", "Bash", "b2", tool_input=command))
    changed = UserConfig.model_validate(
        {"prototype_verification": {"commands": ["other"], "tier": "block"}}
    )
    pipeline.handle_hook_request(
        hook(tmp_path, "PostToolUse", "Bash", "b2", tool_input=command, tool_response=response),
        config=changed,
    )
    assert decision(run(tmp_path, hook(tmp_path, "Stop"))) == "block"


def test_session_in_subdirectory_clears_from_same_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    child = tmp_path / "child"
    child.mkdir()
    command = {"command": "pytest -q"}
    response = {"stdout": "ok", "stderr": "", "interrupted": False}
    edit = {"file_path": str(child / "a.py")}
    run(child, hook(child, "PostToolUse", "Edit", "e1", tool_input=edit))
    assert decision(run(child, hook(child, "Stop"))) == "block"
    run(child, hook(child, "PreToolUse", "Bash", "b1", tool_input=command))
    run(
        child,
        hook(child, "PostToolUse", "Bash", "b1", tool_input=command, tool_response=response),
    )
    assert decision(run(child, hook(child, "Stop"))) == "allow"


class _BrokenTracker(ObligationTracker):
    def observe(self, event: HookEvent, config: PrototypeVerificationConfig | None) -> bool:
        raise RuntimeError("tracker failure")


def test_tracker_failure_still_evaluates_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KEY", "x")
    monkeypatch.setattr(pipeline, "_obligations", _BrokenTracker())
    write_rule(
        tmp_path,
        "block-writes",
        """
type: decide
name: block-writes
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: block
tier: block
""",
    )
    fn, recorder = decide_fn('{"outcome": "violation"}')
    config = CONFIG.model_copy(
        update={"prototype_verification": PrototypeVerificationConfig(commands=["pytest -q"])}
    )
    result = pipeline.handle_hook_request(make_request(tmp_path), config=config, decide_fn=fn)
    assert recorder.call_count == 1
    assert "deny" in str(result["stdout"])
