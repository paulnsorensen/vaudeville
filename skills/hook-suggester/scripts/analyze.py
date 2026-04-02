#!/usr/bin/env python3
"""Analyze session logs to suggest hooks based on observed usage patterns.

Queries the DuckDB analytics database and identifies opportunities for
hook-based enforcement. Outputs JSON suggestions that can be fed to
add-hook for implementation.

Usage: python3 analyze.py [--days N] [--min-occurrences N] [--json]
"""

import json
import os
import subprocess
import sys

DB_PATH = os.path.expanduser("~/.claude/analytics/sessions.duckdb")
DEFAULT_DAYS = 14
DEFAULT_MIN_OCCURRENCES = 3


def query(sql):
    """Run a DuckDB query and return parsed JSON results."""
    try:
        result = subprocess.run(
            ["duckdb", DB_PATH, "-json", "-c", sql],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        print("ERROR: duckdb not found on PATH", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        print(
            f"WARNING: duckdb query failed (exit {result.returncode})",
            file=sys.stderr,
        )
        if result.stderr:
            print(f"  {result.stderr.strip()[:200]}", file=sys.stderr)
        return []
    raw = result.stdout.strip()
    if not raw or raw == "[{]":
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"WARNING: Failed to parse DuckDB JSON output: {e}", file=sys.stderr)
        return []


def check_dangerous_bash(days, min_occ):
    """Detect dangerous bash commands that should be guarded."""
    rows = query(f"""
        SELECT
            bash_cmd,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND (
            bash_cmd LIKE '%rm -rf%'
            OR (bash_cmd LIKE '%git push%--force%'
                AND bash_cmd NOT LIKE '%--force-with-lease%')
            OR bash_cmd LIKE '%DROP %'
            OR bash_cmd LIKE '%> /dev/%'
            OR bash_cmd LIKE '%chmod 777%'
            OR bash_cmd LIKE '%--no-verify%'
          )
        GROUP BY bash_cmd
        HAVING count(*) >= {min_occ}
        ORDER BY uses DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    examples = [r["bash_cmd"][:80] for r in rows]
    total = sum(int(r["uses"]) for r in rows)
    return {
        "id": "dangerous-bash",
        "event": "PreToolUse",
        "priority": "high",
        "title": "Guard dangerous bash commands",
        "description": (
            f"Found {total} uses of dangerous bash patterns across "
            f"{len(rows)} commands in the last {days} days."
        ),
        "examples": examples,
        "hook_type": "safety",
        "suggested_action": "block",
    }


def check_tool_misuse(days, min_occ):
    """Detect bash used for tasks that have dedicated tools."""
    rows = query(f"""
        SELECT
            CASE
                WHEN bash_cmd LIKE 'cat %' OR bash_cmd LIKE 'head %'
                    OR bash_cmd LIKE 'tail %' THEN 'cat/head/tail → Read tool'
                WHEN bash_cmd LIKE 'grep %' OR bash_cmd LIKE 'rg %'
                    OR bash_cmd LIKE 'egrep %' THEN 'grep/rg → Grep tool'
                WHEN bash_cmd LIKE 'find %' OR bash_cmd LIKE 'fd %'
                    THEN 'find/fd → Glob tool'
                WHEN bash_cmd LIKE 'sed %' OR bash_cmd LIKE '%sed -i%'
                    THEN 'sed → Edit tool'
                WHEN bash_cmd LIKE 'echo %>>%' OR bash_cmd LIKE 'echo %>%'
                    OR (bash_cmd LIKE 'cat %' AND bash_cmd LIKE '%>%')
                    THEN 'echo/cat redirect → Write tool'
            END AS misuse_type,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY misuse_type
        HAVING misuse_type IS NOT NULL AND count(*) >= {min_occ}
        ORDER BY uses DESC;
    """)
    if not rows:
        return None
    total = sum(int(r["uses"]) for r in rows)
    misuses = [f"{r['misuse_type']} ({r['uses']}x)" for r in rows]
    return {
        "id": "tool-misuse",
        "event": "PreToolUse",
        "priority": "medium",
        "title": "Redirect bash to dedicated tools",
        "description": (
            f"Found {total} bash calls that should use dedicated tools. "
            f"A PreToolUse hook can warn Claude to use the right tool."
        ),
        "examples": misuses,
        "hook_type": "quality",
        "suggested_action": "warn",
    }


def check_high_error_tools(days, min_occ):
    """Detect tools with high error rates."""
    rows = query(f"""
        SELECT
            tu.tool_name,
            count(*) AS total,
            sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END) AS errors,
            round(
                sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END)
                * 100.0 / count(*), 1
            ) AS error_pct
        FROM tool_uses tu
        JOIN tool_results tr ON tu.tool_use_id = tr.tool_use_id
        WHERE tu.timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY tu.tool_name
        HAVING count(*) >= {min_occ} AND error_pct > 20
        ORDER BY error_pct DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    tools = [
        f"{r['tool_name']} ({r['error_pct']}% errors, {r['total']} calls)" for r in rows
    ]
    return {
        "id": "high-error-tools",
        "event": "PostToolUse",
        "priority": "medium",
        "title": "Add validation for error-prone tools",
        "description": (
            f"Found {len(rows)} tools with >20% error rate. "
            f"PostToolUse hooks can catch common failure patterns."
        ),
        "examples": tools,
        "hook_type": "quality",
        "suggested_action": "warn",
    }


def check_permission_friction(days, min_occ):
    """Detect frequent permission denials."""
    rows = query(f"""
        SELECT
            substr(content, 1, 100) AS denial,
            count(*) AS denials
        FROM permission_denials
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY denial
        HAVING count(*) >= {min_occ}
        ORDER BY denials DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    total = sum(int(r["denials"]) for r in rows)
    return {
        "id": "permission-friction",
        "event": "PreToolUse",
        "priority": "low",
        "title": "Reduce permission friction",
        "description": (
            f"Found {total} permission denials across {len(rows)} patterns. "
            f"Consider adding allowlist entries or a PreToolUse hook that "
            f"catches these before they hit the permission prompt."
        ),
        "examples": [r["denial"][:80] for r in rows],
        "hook_type": "workflow",
        "suggested_action": "info",
    }


def check_missing_quality_hooks(days):
    """Detect if Stop hooks are underused."""
    hook_rows = query(f"""
        SELECT count(*) AS cnt
        FROM stop_hooks
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY;
    """)
    stop_rows = query(f"""
        SELECT count(*) AS cnt
        FROM stop_events
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY;
    """)
    hook_count = int(hook_rows[0]["cnt"]) if hook_rows else 0
    stop_count = int(stop_rows[0]["cnt"]) if stop_rows else 0

    if stop_count == 0:
        return None

    ratio = hook_count / stop_count if stop_count > 0 else 0
    if ratio > 0.5:
        return None

    return {
        "id": "missing-quality-hooks",
        "event": "Stop",
        "priority": "high",
        "title": "Add Stop hooks for quality enforcement",
        "description": (
            f"Only {hook_count}/{stop_count} stops "
            f"({ratio * 100:.0f}%) triggered quality hooks. "
            f"Stop hooks catch hedging, premature completion, and "
            f"unverified claims before they reach you."
        ),
        "examples": [],
        "hook_type": "quality",
        "suggested_action": "block",
    }


def check_hook_failures(days, min_occ):
    """Detect hooks that frequently error."""
    error_rows = query(f"""
        SELECT
            json_extract_string(err_element, '$') AS err,
            count(*) AS cnt
        FROM stop_hooks,
             unnest(json_extract(hookErrors, '$[*]')) AS t(err_element)
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND hookErrors IS NOT NULL
          AND json_array_length(hookErrors) > 0
        GROUP BY err
        HAVING count(*) >= {min_occ}
        ORDER BY cnt DESC
        LIMIT 5;
    """)
    if not error_rows:
        return None
    return {
        "id": "hook-failures",
        "event": "Stop",
        "priority": "high",
        "title": "Fix failing hooks",
        "description": (
            f"Found {len(error_rows)} hook error patterns. "
            f"Broken hooks silently pass, defeating enforcement."
        ),
        "examples": [r["err"][:100] for r in error_rows],
        "hook_type": "maintenance",
        "suggested_action": "fix",
    }


def check_code_write_volume(days, min_occ):
    """Detect languages with high code write volume."""
    rows = query(f"""
        SELECT
            CASE
                WHEN file_path LIKE '%.py' THEN 'Python'
                WHEN file_path LIKE '%.ts' OR file_path LIKE '%.tsx' THEN 'TypeScript'
                WHEN file_path LIKE '%.js' OR file_path LIKE '%.jsx' THEN 'JavaScript'
                WHEN file_path LIKE '%.rs' THEN 'Rust'
                WHEN file_path LIKE '%.go' THEN 'Go'
            END AS lang,
            count(*) AS writes
        FROM tool_uses
        WHERE tool_name IN ('Edit', 'Write')
          AND file_path IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY lang
        HAVING lang IS NOT NULL AND count(*) >= {min_occ}
        ORDER BY writes DESC;
    """)
    if not rows:
        return None
    langs = [f"{r['lang']} ({r['writes']} writes)" for r in rows]
    total = sum(int(r["writes"]) for r in rows)
    return {
        "id": "auto-format",
        "event": "PostToolUse",
        "priority": "low",
        "title": "Auto-format on file writes",
        "description": (
            f"Found {total} code file writes across {len(rows)} languages. "
            f"A PostToolUse hook can auto-run formatters after Edit/Write."
        ),
        "examples": langs,
        "hook_type": "workflow",
        "suggested_action": "info",
    }


def check_repeated_bash_patterns(days, min_occ):
    """Find frequently repeated bash commands that could be hooks."""
    rows = query(f"""
        SELECT
            bash_cmd AS cmd,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND length(bash_cmd) > 20
        GROUP BY bash_cmd
        HAVING count(*) >= {min_occ * 3}
        ORDER BY uses DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    cmds = [f"{r['cmd'][:80]} ({r['uses']}x)" for r in rows]
    return {
        "id": "repeated-commands",
        "event": "SessionStart",
        "priority": "low",
        "title": "Automate repeated commands",
        "description": (
            f"Found {len(rows)} bash commands repeated {min_occ * 3}+ times. "
            f"Consider automating via SessionStart or UserPromptSubmit hooks."
        ),
        "examples": cmds,
        "hook_type": "workflow",
        "suggested_action": "info",
    }


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

    analyzers = [
        check_dangerous_bash,
        check_tool_misuse,
        check_high_error_tools,
        check_permission_friction,
        check_missing_quality_hooks,
        check_hook_failures,
        check_code_write_volume,
        check_repeated_bash_patterns,
    ]

    suggestions = []
    for analyzer in analyzers:
        try:
            # check_missing_quality_hooks only takes days
            if analyzer == check_missing_quality_hooks:
                result = analyzer(days)
            else:
                result = analyzer(days, min_occ)
            if result:
                suggestions.append(result)
        except Exception as e:
            print(f"WARNING: {analyzer.__name__} failed: {e}", file=sys.stderr)

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: priority_order.get(s["priority"], 3))

    if as_json:
        print(json.dumps(suggestions, indent=2))
    else:
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


if __name__ == "__main__":
    main()
