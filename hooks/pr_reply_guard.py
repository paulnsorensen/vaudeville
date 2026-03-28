#!/usr/bin/env python3
"""PostToolUse hook — blocks deferral language in PR review replies.

Runs deferral-detector rule via daemon socket.
Only evaluates PR reply tools; allows all others.
Fails open: if daemon is unavailable, allows the tool use.
"""
import json
import os
import sys

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from vaudeville.core.client import VaudevilleClient  # noqa: E402

PR_REPLY_TOOLS = {
    "add_reply_to_pull_request_comment",
    "pull_request_review_write",
    "add_comment_to_pending_review",
    "add_issue_comment",
}


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    if tool_name not in PR_REPLY_TOOLS:
        print("{}")
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    body = tool_input.get("body") or tool_input.get("message") or ""

    if not body:
        print("{}")
        sys.exit(0)

    session_id = hook_input.get("session_id", "unknown")
    client = VaudevilleClient(session_id)

    result = client.classify("deferral-detector", {"text": body})
    if result is None:
        print("{}")
        sys.exit(0)

    if result.verdict == "violation":
        if result.action == "log":
            print(json.dumps({}), end="")
            print(f"[vaudeville] deferral-detector: {result.reason}", file=sys.stderr)
        elif result.action == "warn":
            print(json.dumps({
                "decision": "block",
                "reason": result.reason,
                "systemMessage": f"Warning — deferral detected: {result.reason}",
            }))
            sys.exit(0)
        else:  # block (default)
            print(json.dumps({
                "decision": "block",
                "reason": result.reason,
                "systemMessage": (
                    f"Deferral detected: {result.reason}. "
                    "Address the reviewer's feedback directly in this PR — "
                    "don't defer to a follow-up."
                ),
            }))
            sys.exit(0)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
