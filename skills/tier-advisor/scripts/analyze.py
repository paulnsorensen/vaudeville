#!/usr/bin/env python3
"""Compute per-rule metrics with user-agreement proxy for tier-advisor.

Joins vaudeville_verdicts against raw_entries to find the next user message
after each violation, then classifies user response as agreement, disagreement,
or uncertain.

Output: JSON array of per-rule metric objects to stdout.
"""

import json
import os
import subprocess
import sys

DB_PATH = os.path.expanduser("~/.claude/analytics/sessions.duckdb")

CORRECTION_KEYWORDS = [
    "no",
    "wrong",
    "that's not",
    "that is not",
    "incorrect",
    "don't",
    "stop",
    "undo",
    "revert",
    "not what I",
]

ACK_KEYWORDS = [
    "fixed",
    "good catch",
    "thanks",
    "thank you",
    "nice catch",
    "right",
    "agreed",
    "yes",
    "correct",
]

PUSHBACK_KEYWORDS = [
    "no that's fine",
    "ignore that",
    "that's okay",
    "it's fine",
    "false positive",
    "not a problem",
    "skip that",
    "leave it",
]


def query(sql: str) -> list[dict]:
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
        print(f"ERROR: query failed: {result.stderr[:200]}", file=sys.stderr)
        return []
    raw = result.stdout.strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def compute_rule_metrics() -> list[dict]:
    sql = """
    SELECT
        rule,
        count(*) as total_evals,
        count(*) FILTER (WHERE verdict = 'violation') as violations,
        count(*) FILTER (WHERE verdict = 'clean') as cleans,
        avg(confidence) as avg_confidence,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY confidence) as p50_confidence,
        min(ts) as first_seen,
        max(ts) as last_seen
    FROM vaudeville_verdicts
    GROUP BY rule
    ORDER BY total_evals DESC
    """
    return query(sql)


def compute_agreement_proxy() -> dict[str, dict]:
    """Approximate user agreement by checking next user text after violations.

    Returns per-rule agreement stats: {rule: {agreed, disagreed, uncertain}}.
    """
    violations_sql = """
    SELECT
        v.ts,
        v.rule,
        v.input_snippet,
        v.reason
    FROM vaudeville_verdicts v
    WHERE v.verdict = 'violation'
    AND v.input_snippet != ''
    ORDER BY v.ts
    """
    violations = query(violations_sql)
    if not violations:
        return {}

    next_msg_sql = """
    SELECT
        r.timestamp as ts,
        json_extract_string(r.message, '$.content[0].text') as user_text,
        json_extract_string(r.message, '$.content[0].type') as content_type
    FROM raw_entries r
    WHERE r.type = 'user'
    AND json_extract_string(r.message, '$.content[0].type') = 'text'
    AND length(json_extract_string(r.message, '$.content[0].text')) > 0
    ORDER BY r.timestamp
    """
    user_msgs = query(next_msg_sql)

    user_msg_times = [(m["ts"], m.get("user_text", "")) for m in user_msgs if m["ts"]]

    agreement: dict[str, dict[str, int]] = {}

    for v in violations:
        rule = v["rule"]
        if rule not in agreement:
            agreement[rule] = {"agreed": 0, "disagreed": 0, "uncertain": 0}

        v_ts = v["ts"]
        next_text = _find_next_user_message(v_ts, user_msg_times)
        if not next_text:
            agreement[rule]["uncertain"] += 1
            continue

        lower = next_text.lower()
        if any(kw in lower for kw in PUSHBACK_KEYWORDS):
            agreement[rule]["disagreed"] += 1
        elif any(kw in lower for kw in ACK_KEYWORDS):
            agreement[rule]["agreed"] += 1
        elif any(kw in lower for kw in CORRECTION_KEYWORDS):
            agreement[rule]["agreed"] += 1
        else:
            agreement[rule]["uncertain"] += 1

    return agreement


def _find_next_user_message(
    violation_ts: str, user_msgs: list[tuple[str, str]]
) -> str | None:
    for ts, text in user_msgs:
        if ts > violation_ts:
            return text
    return None


def build_analysis() -> list[dict]:
    metrics = compute_rule_metrics()
    agreement = compute_agreement_proxy()

    results = []
    for m in metrics:
        rule = m["rule"]
        total = m["total_evals"]
        violations = m["violations"]
        violation_rate = violations / total if total > 0 else 0.0

        ag = agreement.get(rule, {"agreed": 0, "disagreed": 0, "uncertain": 0})
        evaluated = ag["agreed"] + ag["disagreed"]
        agreement_rate = ag["agreed"] / evaluated if evaluated > 0 else None

        results.append(
            {
                "rule": rule,
                "total_evals": total,
                "violations": violations,
                "cleans": m["cleans"],
                "violation_rate": round(violation_rate, 4),
                "avg_confidence": round(m["avg_confidence"], 3),
                "p50_confidence": round(m["p50_confidence"], 3),
                "first_seen": m["first_seen"],
                "last_seen": m["last_seen"],
                "agreement_rate": (
                    round(agreement_rate, 3) if agreement_rate is not None else None
                ),
                "agreement_evaluated": evaluated,
                "agreement_agreed": ag["agreed"],
                "agreement_disagreed": ag["disagreed"],
                "agreement_uncertain": ag["uncertain"],
            }
        )

    return results


def main() -> None:
    results = build_analysis()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
