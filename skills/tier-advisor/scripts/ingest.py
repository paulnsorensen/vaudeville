#!/usr/bin/env python3
"""Ingest vaudeville JSONL logs into DuckDB for tier-advisor analysis.

Reads ~/.vaudeville/logs/events.jsonl and violations.jsonl,
deduplicates by (ts, rule, input_snippet_hash), and upserts into
~/.claude/analytics/sessions.duckdb as table vaudeville_verdicts.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

LOGS_DIR = Path(os.path.expanduser("~/.vaudeville/logs"))
DB_PATH = os.path.expanduser("~/.claude/analytics/sessions.duckdb")

EVENTS_FILE = LOGS_DIR / "events.jsonl"
VIOLATIONS_FILE = LOGS_DIR / "violations.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def snippet_hash(snippet: str | None) -> str:
    text = snippet or ""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_records() -> list[dict]:
    records = []
    seen = set()

    for row in read_jsonl(EVENTS_FILE):
        key = (row.get("ts", ""), row.get("rule", ""), "")
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "ts": row.get("ts", ""),
                "rule": row.get("rule", ""),
                "verdict": row.get("verdict", ""),
                "confidence": row.get("confidence", 0.0),
                "latency_ms": row.get("latency_ms", 0.0),
                "prompt_chars": row.get("prompt_chars", 0),
                "reason": "",
                "input_snippet": "",
                "snippet_hash": "",
            }
        )

    for row in read_jsonl(VIOLATIONS_FILE):
        snippet = row.get("input_snippet", "")
        shash = snippet_hash(snippet)
        key = (row.get("ts", ""), row.get("rule", ""), shash)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "ts": row.get("ts", ""),
                "rule": row.get("rule", ""),
                "verdict": row.get("verdict", "violation"),
                "confidence": row.get("confidence", 0.0),
                "latency_ms": row.get("latency_ms", 0.0),
                "prompt_chars": row.get("prompt_chars", 0),
                "reason": row.get("reason", ""),
                "input_snippet": snippet[:500],
                "snippet_hash": shash,
            }
        )

    return records


def ingest(records: list[dict]) -> int:
    if not records:
        print("No records to ingest.")
        return 0

    tmp = Path("/tmp/vaudeville_verdicts_staging.jsonl")
    with open(tmp, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    sql = f"""
    CREATE TABLE IF NOT EXISTS vaudeville_verdicts (
        ts VARCHAR,
        rule VARCHAR,
        verdict VARCHAR,
        confidence DOUBLE,
        latency_ms DOUBLE,
        prompt_chars INTEGER,
        reason VARCHAR,
        input_snippet VARCHAR,
        snippet_hash VARCHAR
    );

    DELETE FROM vaudeville_verdicts;

    INSERT INTO vaudeville_verdicts
    SELECT * FROM read_json('{tmp}',
        columns={{
            ts: 'VARCHAR',
            rule: 'VARCHAR',
            verdict: 'VARCHAR',
            confidence: 'DOUBLE',
            latency_ms: 'DOUBLE',
            prompt_chars: 'INTEGER',
            reason: 'VARCHAR',
            input_snippet: 'VARCHAR',
            snippet_hash: 'VARCHAR'
        }}
    );
    """

    result = subprocess.run(
        ["duckdb", DB_PATH, "-c", sql],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        print(f"ERROR: DuckDB ingest failed: {result.stderr[:300]}", file=sys.stderr)
        return 0

    tmp.unlink(missing_ok=True)

    count_result = subprocess.run(
        [
            "duckdb",
            DB_PATH,
            "-json",
            "-c",
            "SELECT count(*) as cnt FROM vaudeville_verdicts",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if count_result.returncode == 0:
        rows = json.loads(count_result.stdout.strip())
        count = rows[0]["cnt"] if rows else 0
        print(f"Ingested {count} records into vaudeville_verdicts.")
        return count
    return len(records)


def main() -> None:
    if not LOGS_DIR.exists():
        print(f"No logs directory at {LOGS_DIR}", file=sys.stderr)
        sys.exit(1)

    records = build_records()
    count = ingest(records)
    if count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
