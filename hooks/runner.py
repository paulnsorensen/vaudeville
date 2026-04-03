#!/usr/bin/env python3
"""Generic hook runner — evaluates rules against hook input.

Usage:
  python3 runner.py --event Stop          # auto-discover rules for event
  python3 runner.py <rule-name> [...]     # explicit rule names (legacy)

Reads Claude Code hook JSON from stdin, loads rules (layered: bundled ->
~/.vaudeville/rules/ -> project/.vaudeville/rules/), classifies via daemon
socket, and returns the first blocking verdict (or passes if all clean).

Fails open: if daemon is unavailable or input is missing, allows the hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    from vaudeville.core.client import VaudevilleClient  # noqa: E402
except ImportError as _exc:
    print(f"[vaudeville] cannot import client ({_exc}) — fail open", file=sys.stderr)
    print("{}")
    sys.exit(0)

MIN_TEXT_LENGTH = 100
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


def _load_rules_for_event(event: str) -> list:
    """Auto-discover all rules matching an event via layered resolution."""
    from vaudeville.core.rules import load_rules_layered  # noqa: E402

    project_root = _find_project_root()
    all_rules = load_rules_layered(PLUGIN_ROOT, project_root)
    matching = [r for r in all_rules.values() if r.event == event]
    return sorted(matching, key=lambda r: r.name)


def _run() -> None:
    args = sys.argv[1:]

    # Parse --event flag for auto-discovery mode
    event = None
    rule_names: list[str] = []
    if len(args) >= 2 and args[0] == "--event":
        event = args[1]
    else:
        rule_names = args

    if not event and not rule_names:
        print("[vaudeville] runner: no rules or --event specified", file=sys.stderr)
        print("{}")
        sys.exit(0)

    _dbg("hook fired — rules: %s", rule_names)

    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        _dbg("no valid JSON on stdin — pass")
        print("{}")
        sys.exit(0)

    hook_type = hook_input.get("hook_type", "?")
    _dbg("hook=%s", hook_type)

    client = VaudevilleClient()

    if event:
        _run_event_rules(event, hook_input, client)
    else:
        _run_named_rules(rule_names, hook_input, client)


def _run_event_rules(event: str, hook_input: dict, client: VaudevilleClient) -> None:
    """Evaluate all rules matching the given event."""
    rules = _load_rules_for_event(event)
    for rule in rules:
        text = extract_text_from_dict(hook_input, rule.context)
        if not text or len(text) < MIN_TEXT_LENGTH:
            continue

        result = client.classify(rule.name, {"text": text})
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


def _run_named_rules(
    rule_names: list[str], hook_input: dict, client: VaudevilleClient
) -> None:
    """Evaluate explicitly named rules (legacy mode)."""
    # Build classify tasks for all valid rules
    tasks: list[tuple[str, Rule]] = []
    for name in rule_names:
        rule = load_rule(name)
        if rule is None:
            continue
        text = extract_text_from_dict(hook_input, rule.context)
        if not text or len(text) < MIN_TEXT_LENGTH:
            _dbg(
                "%s: text too short (%d chars) — skip",
                name,
                len(text) if text else 0,
            )
            continue
        tasks.append((name, rule))

    if not tasks:
        print("{}")
        sys.exit(0)

    # Single rule: no threading overhead
    if len(tasks) == 1:
        name, rule = tasks[0]
        text = extract_text_from_dict(hook_input, rule.context)
        _dbg("%s: classifying %d chars...", name, len(text))
        t0 = time.monotonic()
        result = client.classify(name, {"text": text})
        elapsed_ms = (time.monotonic() - t0) * 1000
        if result and result.verdict == rule.labels[0]:
            _dbg(
                "%s: verdict=%s action=%s (%.0fms) reason=%r",
                name,
                result.verdict,
                result.action,
                elapsed_ms,
                result.reason,
            )
            print(
                json.dumps(
                    verdict_to_hook_response(
                        rule.name, rule.message, result.reason, result.action
                    )
                )
            )
            sys.exit(0)
        _dbg("%s: clean (%.0fms)", name, elapsed_ms)
        print("{}")
        sys.exit(0)

    # Multiple rules: dispatch concurrently, first violation wins
    def _classify(name: str, rule: Rule) -> tuple[str, str, str, str] | None:
        text = extract_text_from_dict(hook_input, rule.context)
        _dbg("%s: classifying %d chars...", name, len(text))
        t0 = time.monotonic()
        result = client.classify(name, {"text": text})
        elapsed_ms = (time.monotonic() - t0) * 1000
        if result and result.verdict == rule.labels[0]:
            _dbg(
                "%s: verdict=%s action=%s (%.0fms) reason=%r",
                name,
                result.verdict,
                result.action,
                elapsed_ms,
                result.reason,
            )
            return (rule.name, rule.message, result.reason, rule.action)
        _dbg("%s: clean (%.0fms)", name, elapsed_ms)
        return None

    pool = ThreadPoolExecutor(max_workers=len(tasks))
    futures = {pool.submit(_classify, n, r): n for n, r in tasks}
    for future in as_completed(futures):
        hit = future.result()
        if hit:
            name, message, reason, action = hit
            print(json.dumps(verdict_to_hook_response(name, message, reason, action)))
            pool.shutdown(wait=False, cancel_futures=True)
            sys.exit(0)
    pool.shutdown(wait=False, cancel_futures=True)

    _dbg("all rules clean — pass")
    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[vaudeville] runner crashed ({exc}) — fail open", file=sys.stderr)
        print("{}")
        sys.exit(0)
