#!/usr/bin/env python3
"""Generic hook runner — evaluates rules against hook input.

Usage:
  python3 runner.py --event Stop          # auto-discover rules for event

Reads Claude Code hook JSON from stdin, loads rules
(~/.vaudeville/rules/ -> project/.vaudeville/rules/), classifies via daemon
socket, and returns the first blocking verdict (or passes if all clean).

Fails open: if daemon is unavailable or input is missing, allows the hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

try:
    from vaudeville.core.client import VaudevilleClient  # noqa: E402
except ImportError as _exc:
    print(f"[vaudeville] cannot import client ({_exc}) — fail open", file=sys.stderr)
    print("{}")
    sys.exit(0)

MIN_TEXT_LENGTH = 50
_DEBUG = os.environ.get("VAUDEVILLE_DEBUG", "") == "1"


def _dbg(msg: str, *args: object) -> None:
    if _DEBUG:
        print(f"[vaudeville:debug] {msg % args if args else msg}", file=sys.stderr)


def _find_project_root() -> str | None:
    """Find the git working tree root, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def extract_field(data: dict, dotted_path: str) -> str:
    """Walk a dotted path like 'tool_input.body' into nested dicts."""
    current = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
        if current is None:
            return ""
    return "" if current is None else str(current)


def extract_text_from_dict(hook_input: dict, context: list) -> str:
    """Extract classifiable text from hook input using rule context entries."""
    if not context:
        return ""

    for entry in context:
        if isinstance(entry, dict) and "field" in entry:
            text = extract_field(hook_input, entry["field"])
            if text:
                return text
        elif isinstance(entry, str):
            text = extract_field(hook_input, entry)
            if text:
                return text

    return ""


def verdict_to_hook_response(
    name: str, message_template: str, reason: str, action: str
) -> dict:
    """Translate a daemon verdict into a Claude Code hook response."""
    message = message_template.replace("{reason}", reason)

    if action == "log":
        print(f"[vaudeville] {name}: {reason}", file=sys.stderr)
        return {}
    elif action == "warn":
        return {
            "decision": "warn",
            "reason": reason,
            "systemMessage": (
                f"\U0001fa9d vaudeville hook [{name}] warned about: {message}"
            ),
        }
    else:  # block (default)
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": (
                f"\U0001fa9d vaudeville hook [{name}] prevented response: {message}"
            ),
        }


def main() -> None:
    try:
        _run()
    except Exception as exc:
        print(f"[vaudeville] runner crashed ({exc}) — fail open", file=sys.stderr)
        print("{}")
        sys.exit(0)


def _load_rules_for_event(event: str) -> list:
    """Auto-discover all rules matching an event via layered resolution."""
    from vaudeville.core.rules import load_rules_layered  # noqa: E402

    project_root = _find_project_root()
    all_rules = load_rules_layered(project_root)
    matching = [r for r in all_rules.values() if r.event == event]
    return sorted(matching, key=lambda r: r.name)


def _run() -> None:
    args = sys.argv[1:]

    # Parse --event flag
    event = None
    if len(args) >= 2 and args[0] == "--event":
        event = args[1]

    if not event:
        print("[vaudeville] runner: --event required", file=sys.stderr)
        print("{}")
        sys.exit(0)

    _dbg("hook fired — event: %s", event)

    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        _dbg("no valid JSON on stdin — pass")
        print("{}")
        sys.exit(0)

    hook_type = hook_input.get("hook_type", "?")
    _dbg("hook=%s", hook_type)

    client = VaudevilleClient()
    _run_event_rules(event, hook_input, client)


def _run_event_rules(event: str, hook_input: dict, client: VaudevilleClient) -> None:
    """Evaluate all rules matching the given event."""
    rules = _load_rules_for_event(event)
    for rule in rules:
        text = extract_text_from_dict(hook_input, rule.context)
        if not text or len(text) < MIN_TEXT_LENGTH:
            continue

        context_str = rule.resolve_context(hook_input, PLUGIN_ROOT)
        prompt = rule.format_prompt(text, context_str)

        result = client.classify(prompt, rule=rule.name)
        if result is None:
            continue

        if result.verdict == "violation" and result.confidence >= rule.threshold:
            response = verdict_to_hook_response(
                rule.name, rule.message, result.reason, rule.action
            )
            print(json.dumps(response))
            sys.exit(0)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[vaudeville] runner crashed ({exc}) — fail open", file=sys.stderr)
        print("{}")
        sys.exit(0)
