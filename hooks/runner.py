#!/usr/bin/env python3
"""Generic hook runner — forwards harness hook input to the daemon.

Usage:
  python3 runner.py --harness claude-code

Reads hook JSON from stdin, sends it to the vaudeville daemon over the
hook wire, and prints the daemon's response verbatim.

Fails open: if the daemon is unavailable, the harness is unknown, or
stdin is missing/malformed, allows the hook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    str(Path(__file__).resolve().parent.parent),
)

if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from vaudeville.core import VaudevilleClient  # noqa: E402
from vaudeville.core.protocol import GENERIC_ALLOW, HookResponse  # noqa: E402

_ALLOW_OUTPUT: dict[str, HookResponse] = {"claude-code": GENERIC_ALLOW}


def _parse_harness(argv: list[str]) -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", default="claude-code")
    args, _unknown = parser.parse_known_args(argv)
    return str(args.harness)


def _exit_allow(harness: str) -> NoReturn:
    response = _ALLOW_OUTPUT.get(harness, GENERIC_ALLOW)
    print(response["stdout"])
    sys.exit(response["exit_code"])


def _run() -> None:
    try:
        harness = _parse_harness(sys.argv[1:])
    except SystemExit:
        _exit_allow("claude-code")
    if harness not in _ALLOW_OUTPUT:
        _exit_allow(harness)

    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        _exit_allow(harness)

    if not isinstance(hook_input, dict):
        _exit_allow(harness)

    request: dict[str, object] = {
        "op": "hook",
        "harness": harness,
        "event": str(hook_input.get("hook_event_name", "")),
        "cwd": str(hook_input.get("cwd", "")),
        "payload": hook_input,
    }

    response = VaudevilleClient().hook(request)
    if response is None:
        _exit_allow(harness)

    print(response["stdout"])
    sys.exit(response["exit_code"])


def main() -> None:
    try:
        _run()
    except Exception as exc:
        print(f"[vaudeville] runner crashed ({exc}) — fail open", file=sys.stderr)
        _exit_allow("claude-code")


if __name__ == "__main__":
    main()
