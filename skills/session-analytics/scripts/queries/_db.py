"""Shared DuckDB query runner for session analytics scripts."""

import json
import os
import subprocess
import sys

DB_PATH = os.path.expanduser("~/.claude/analytics/sessions.duckdb")


def query(sql: str) -> list[dict]:
    """Run a DuckDB query and return parsed JSON results."""
    if not os.path.exists(DB_PATH):
        print(
            "ERROR: Database not found. Run session-analytics ingestion first.",
            file=sys.stderr,
        )
        print(f"Expected: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
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
        print(f"WARNING: query failed: {result.stderr.strip()[:200]}", file=sys.stderr)
        return []
    raw = result.stdout.strip()
    if not raw or raw == "[{]":
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"WARNING: bad JSON from duckdb: {e}", file=sys.stderr)
        return []


def parse_days(args: list[str], default: int = 14) -> int:
    """Parse --days N from argv."""
    for i, arg in enumerate(args):
        if arg == "--days" and i + 1 < len(args):
            return int(args[i + 1])
    return default


def parse_limit(args: list[str], default: int = 15) -> int:
    """Parse --limit N from argv."""
    for i, arg in enumerate(args):
        if arg == "--limit" and i + 1 < len(args):
            return int(args[i + 1])
    return default


def output(rows: list[dict], args: list[str]) -> None:
    """Print results as JSON (--json) or tab-separated."""
    if "--json" in args:
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("(no results)")
            return
        keys = list(rows[0].keys())
        print("\t".join(keys))
        for row in rows:
            print("\t".join(str(row.get(k, "")) for k in keys))
