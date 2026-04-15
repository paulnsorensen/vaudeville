#!/usr/bin/env python3
"""Analyze session logs to suggest hooks based on observed usage patterns.

Queries the DuckDB analytics database and identifies opportunities for
hook-based enforcement. Outputs JSON suggestions that can be fed to
add-hook for implementation.

Usage: python3 analyze.py [--days N] [--min-occurrences N] [--json]
"""

import json
import os
import sys

from analyzers import (
    DB_PATH,
    DEFAULT_DAYS,
    DEFAULT_MIN_OCCURRENCES,
    check_code_write_volume,
    check_correction_patterns,
    check_dangerous_bash,
    check_high_error_tools,
    check_hook_failures,
    check_missing_quality_hooks,
    check_permission_friction,
    check_permission_tool_waste,
    check_repeated_bash_patterns,
    check_retry_loops,
    check_tool_misuse,
    query,
)

__all__ = [
    "DB_PATH",
    "DEFAULT_DAYS",
    "DEFAULT_MIN_OCCURRENCES",
    "check_code_write_volume",
    "check_correction_patterns",
    "check_dangerous_bash",
    "check_high_error_tools",
    "check_hook_failures",
    "check_missing_quality_hooks",
    "check_permission_friction",
    "check_permission_tool_waste",
    "check_repeated_bash_patterns",
    "check_retry_loops",
    "check_tool_misuse",
    "query",
]

ANALYZERS = [
    check_dangerous_bash,
    check_tool_misuse,
    check_high_error_tools,
    check_permission_friction,
    check_missing_quality_hooks,
    check_hook_failures,
    check_code_write_volume,
    check_repeated_bash_patterns,
    check_correction_patterns,
    check_retry_loops,
    check_permission_tool_waste,
]

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _print_suggestions(suggestions, days):
    if not suggestions:
        print("No hook suggestions found. Your setup looks solid!")
        return

    print(f"\n{'=' * 60}")
    print(f"  HOOK SUGGESTIONS  ({len(suggestions)} found)")
    print(f"  Based on {days} days of session data")
    print(f"{'=' * 60}\n")

    for i, s in enumerate(suggestions, 1):
        icon = {"high": "!!!", "medium": " ! ", "low": " . "}
        pri = icon.get(s["priority"], "   ")
        print(f"[{pri}] {i}. {s['title']}")
        print(f"      Event: {s['event']}  |  Type: {s['hook_type']}")
        print(f"      {s['description']}")
        if s["examples"]:
            print("      Examples:")
            for ex in s["examples"][:5]:
                print(f"        - {ex}")
        print()


def main():
    args = sys.argv[1:]
    days = DEFAULT_DAYS
    min_occ = DEFAULT_MIN_OCCURRENCES
    as_json = "--json" in args

    for i, arg in enumerate(args):
        if arg == "--days" and i + 1 < len(args):
            days = int(args[i + 1])
        elif arg == "--min-occurrences" and i + 1 < len(args):
            min_occ = int(args[i + 1])

    if not os.path.exists(DB_PATH):
        print("ERROR: Database not found. Run session-analytics ingestion first.")
        print(f"Expected: {DB_PATH}")
        sys.exit(1)

    suggestions = []
    for analyzer in ANALYZERS:
        try:
            result = analyzer(days, min_occ)
            if result:
                suggestions.append(result)
        except Exception as e:
            print(f"WARNING: {analyzer.__name__} failed: {e}", file=sys.stderr)

    suggestions.sort(key=lambda s: PRIORITY_ORDER.get(s["priority"], 3))

    if as_json:
        print(json.dumps(suggestions, indent=2))
    else:
        _print_suggestions(suggestions, days)


if __name__ == "__main__":
    main()
