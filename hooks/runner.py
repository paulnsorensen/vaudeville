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
import sys

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

try:
    from vaudeville.core import (
        ClassifyResponse,
        Rule,
        VaudevilleClient,
        find_project_root,
        prepare_text,
    )  # noqa: E402
except ImportError as _exc:
    print(f"[vaudeville] cannot import client ({_exc}) — fail open", file=sys.stderr)
    print("{}")
    sys.exit(0)

MIN_TEXT_LENGTH = 50
_DEBUG = os.environ.get("VAUDEVILLE_DEBUG", "") == "1"


def _dbg(msg: str, *args: object) -> None:
    if _DEBUG:
        print(f"[vaudeville:debug] {msg % args if args else msg}", file=sys.stderr)


def extract_field(data: dict[str, object], dotted_path: str) -> str:
    """Walk a dotted path like 'tool_input.body' into nested dicts."""
    current: object = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
        if current is None:
            return ""
    return str(current)


def extract_text_from_dict(hook_input: dict, context: list) -> str:
    """Extract classifiable text from hook input using rule context entries."""
    if not context:
        return ""

    for entry in context:
        if isinstance(entry, dict) and "field" in entry:
            text = extract_field(hook_input, entry["field"])
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
    from vaudeville.core import load_rules_layered  # noqa: E402

    project_root = find_project_root()
    all_rules = load_rules_layered(project_root)
    matching = [r for r in all_rules.values() if r.event == event]
    return sorted(matching, key=lambda r: r.name)


def _run() -> None:
    if os.environ.get("VAUDEVILLE_SKIP", "") == "1":
        _dbg("VAUDEVILLE_SKIP=1 — bypassing all rules")
        print("{}")
        sys.exit(0)

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


_CONDENSE_MAX_CHARS = (
    500_000  # skip condense above this — daemon would mostly pass through
)


def _maybe_condense(text: str, event: str, client: VaudevilleClient) -> str:
    """Condense text via SLM pre-pass for Stop events. Fail-open."""
    if event != "Stop":
        return text
    _dbg("condensing %d chars for Stop event", len(text))
    if len(text) > _CONDENSE_MAX_CHARS:
        _dbg(
            "skipping condense: %d chars exceeds %d limit",
            len(text),
            _CONDENSE_MAX_CHARS,
        )
        return text
    return client.condense(text)


def _dispatch_violation(rule: Rule, result: ClassifyResponse) -> bool:
    """Handle a tier-aware violation. Returns True if the rule loop should continue."""
    if rule.tier == "shadow":
        _dbg(
            "shadow %s: %s (%.2f)",
            rule.name,
            result.verdict,
            result.confidence,
        )
        return True

    if rule.tier == "warn":
        response = verdict_to_hook_response(
            rule.name, rule.message, result.reason, "warn"
        )
    else:  # enforce (default)
        response = verdict_to_hook_response(
            rule.name, rule.message, result.reason, rule.action
        )
    print(json.dumps(response))
    sys.exit(0)


def _run_event_rules(event: str, hook_input: dict, client: VaudevilleClient) -> None:
    rules = _load_rules_for_event(event)
    condensed: dict[str, str] = {}
    for rule in rules:
        text = extract_text_from_dict(hook_input, rule.context)
        if not text or len(text) < MIN_TEXT_LENGTH:
            continue

        text = prepare_text(text, event)
        if text not in condensed:
            condensed[text] = _maybe_condense(text, event, client)
        text = condensed[text]
        context_str = rule.resolve_context(hook_input, PLUGIN_ROOT)
        prompt, prefix_len = rule.split_prompt(text, context_str)

        result = client.classify(
            prompt, rule=rule.name, prefix_len=prefix_len, tier=rule.tier
        )
        if result is None:
            continue
        if result.verdict != "violation" or result.confidence < rule.threshold:
            continue
        if _dispatch_violation(rule, result):
            continue

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
