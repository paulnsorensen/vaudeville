"""Replay synthetic Claude Code JSON hooks through the live pipeline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from vaudeville.server.hook import handle_hook_request
from vaudeville.server.user_config import UserConfig


def main() -> None:
    config = UserConfig.model_validate(
        {"prototype_verification": {"commands": ["pytest -q"], "tier": "block"}}
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        payload: dict[str, object] = {
            "session_id": "synthetic-session",
            "cwd": str(root),
        }

        def send(
            label: str, event: str, tool: str = "", identity: str = "", **fields: object
        ) -> None:
            hook = {
                **payload,
                "hook_event_name": event,
                "tool_name": tool,
                "tool_use_id": identity,
                **fields,
            }
            request = {
                "op": "hook",
                "harness": "claude-code",
                "event": event,
                "cwd": str(root),
                "payload": hook,
            }
            reply = handle_hook_request(json.loads(json.dumps(request)), config=config)
            body = json.loads(str(reply["stdout"])) if reply["stdout"] else {}
            print(f"{label}: {body.get('decision', 'allow')}")

        edit = {"file_path": str(root / "example.py")}
        command = {"command": "pytest -q"}
        response = {"stdout": "1 passed", "stderr": "", "interrupted": False}
        send("edit", "PostToolUse", "Edit", "e1", tool_input=edit)
        send("pending", "Stop")
        send("verify-start", "PreToolUse", "Bash", "b1", tool_input=command)
        send(
            "verify-done", "PostToolUse", "Bash", "b1", tool_input=command, tool_response=response
        )
        send("clear", "Stop")
        send("new-edit", "PostToolUse", "Edit", "e2", tool_input=edit)
        send("pending-again", "Stop")
        send("stale-start", "PreToolUse", "Bash", "b2", tool_input=command)
        send("newer-edit", "PostToolUse", "Edit", "e3", tool_input=edit)
        send("stale-done", "PostToolUse", "Bash", "b2", tool_input=command, tool_response=response)
        send("still-pending", "Stop")


if __name__ == "__main__":
    main()
