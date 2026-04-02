#!/usr/bin/env python3
"""Generic hook runner — evaluates rules by name against hook input.

Usage: python3 runner.py <rule-name> [rule-name ...]

Reads Claude Code hook JSON from stdin, extracts text per each rule's
`context.field` dotted path, classifies via daemon socket, and returns
the first blocking verdict (or passes if all clean).

Fails open: if daemon is unavailable or input is missing, allows the hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaudeville.core.rules import Rule

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

try:
    from vaudeville.core.client import SOCKET_TEMPLATE, VaudevilleClient  # noqa: E402
except ImportError as _exc:
    print(f"[vaudeville] cannot import client ({_exc}) — fail open", file=sys.stderr)
    print("{}")
    sys.exit(0)

MIN_TEXT_LENGTH = 100


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


def load_rule(name: str) -> Rule | None:
    """Load a rule YAML file by name, searching layered paths (project wins)."""
    import yaml  # deferred — only needed when daemon socket exists

    from vaudeville.core.rules import parse_rule, rules_search_path  # noqa: E402

    filename = f"{name}.yaml"
    project_root = _find_project_root()
    for rules_dir in reversed(rules_search_path(PLUGIN_ROOT, project_root)):
        path = os.path.join(rules_dir, filename)
        if os.path.isfile(path):
            with open(path) as f:
                data = yaml.safe_load(f)
            return parse_rule(data)

    print(f"[vaudeville] rule not found: {name}", file=sys.stderr)
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
            "systemMessage": f"Warning — {message}",
        }
    else:  # block (default)
        return {
            "decision": "block",
            "reason": reason,
            "systemMessage": message,
        }


def main() -> None:
    try:
        _run()
    except Exception as exc:
        print(f"[vaudeville] runner crashed ({exc}) — fail open", file=sys.stderr)
        print("{}")
        sys.exit(0)


def _run() -> None:
    rule_names = sys.argv[1:]
    if not rule_names:
        print("[vaudeville] runner: no rules specified", file=sys.stderr)
        print("{}")
        sys.exit(0)

    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("{}")
        sys.exit(0)

    session_id = hook_input.get("session_id", "unknown")

    # Fast path: if daemon socket doesn't exist, skip (~μs vs timeout)
    socket_path = SOCKET_TEMPLATE.format(session_id=session_id)
    if not os.path.exists(socket_path):
        print("{}")
        sys.exit(0)

    client = VaudevilleClient(session_id)

    for rule_name in rule_names:
        rule = load_rule(rule_name)
        if rule is None:
            continue

        text = extract_text_from_dict(hook_input, rule.context)
        if not text or len(text) < MIN_TEXT_LENGTH:
            continue

        result = client.classify(rule_name, {"text": text})
        if result is None:
            continue

        if result.verdict == rule.labels[0]:
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
