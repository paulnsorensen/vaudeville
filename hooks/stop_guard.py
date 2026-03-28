#!/usr/bin/env python3
"""Stop hook — evaluates assistant response for quality violations.

Runs violation-detector and dismissal-detector rules via daemon socket.
Fails open: if daemon is unavailable, allows the stop.
"""
import json
import os
import sys

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from vaudeville.core.client import VaudevilleClient  # noqa: E402

MIN_MESSAGE_LENGTH = 100
STOP_RULES = ["violation-detector", "dismissal-detector"]


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = hook_input.get("session_id", "unknown")
    text = hook_input.get("last_assistant_message", "")

    if len(text) < MIN_MESSAGE_LENGTH:
        print("{}")
        sys.exit(0)

    client = VaudevilleClient(session_id)

    for rule_name in STOP_RULES:
        result = client.classify(rule_name, {"text": text})
        if result is None:
            continue  # Daemon unavailable — fail open
        if result.verdict == "violation":
            if result.action == "log":
                print(json.dumps({}), end="")
                print(f"[vaudeville] {rule_name}: {result.reason}", file=sys.stderr)
            elif result.action == "warn":
                print(json.dumps({
                    "decision": "block",
                    "reason": result.reason,
                    "systemMessage": f"Warning: {result.reason}",
                }))
                sys.exit(0)
            else:  # block (default)
                print(json.dumps({
                    "decision": "block",
                    "reason": result.reason,
                    "systemMessage": result.reason,
                }))
                sys.exit(0)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
