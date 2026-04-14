---
name: vaudeville:tier-advisor
description: >
  Analyze vaudeville warn logs and eval data to recommend rule tier promotions
  and demotions. Use when the user says "promote rule", "tier advisor",
  "warn log analysis", "should we enforce", "demote rule", "rule metrics",
  "tier recommendation", "warn to block", "shadow to warn", "rule performance",
  "check rule health", "evaluate rule tiers", "promotion analysis",
  or invokes /tier-advisor. Do NOT use for editing rule files — use
  /rule-admin for that.
model: sonnet
context: fork
allowed-tools: Bash(uv:*), Bash(duckdb:*), Bash(python3:*), Read, Glob
---

# tier-advisor

Reads `~/.vaudeville/logs/` JSONL files, ingests into DuckDB, computes
per-rule metrics with user-agreement proxy, and outputs promotion/demotion
recommendations.

## Steps

### 1. Ingest

Run the ingest script to load fresh log data:

```bash
uv run python3 ${CLAUDE_PLUGIN_ROOT}/skills/tier-advisor/scripts/ingest.py
```

This reads `~/.vaudeville/logs/events.jsonl` and `violations.jsonl`,
deduplicates, and replaces table contents in `~/.claude/analytics/sessions.duckdb`
as table `vaudeville_verdicts`.

### 2. Analyze

Run the analysis script to compute per-rule metrics:

```bash
uv run python3 ${CLAUDE_PLUGIN_ROOT}/skills/tier-advisor/scripts/analyze.py
```

This JOINs `vaudeville_verdicts` against `raw_entries` to compute the
agreement proxy (next user message after a violation indicates whether the
user agreed or disagreed with the verdict).

### 3. Report

Run the report script to produce grouped recommendations:

```bash
uv run python3 ${CLAUDE_PLUGIN_ROOT}/skills/tier-advisor/scripts/report.py
```

Output is markdown grouped by recommendation: promote-to-block,
promote-to-warn, demote, insufficient-data.

### 4. Present

Show the report to the user. If the user wants to act on a recommendation,
tell them to use `/rule-admin` to promote, demote, or edit rule tiers.

## Gotchas

- Agreement proxy is approximate — the next-message heuristic misclassifies
  multi-turn corrections where the user responds several messages later
- DuckDB `raw_entries` table may not exist if session-analytics hasn't been
  ingested yet — analyze.py returns empty agreement data, not an error
- Empty `~/.vaudeville/logs/` produces zero records from ingest.py (exit 1),
  which is expected — tell the user to run some sessions first
- Short keywords in agreement matching use word boundaries, but multi-word
  phrases still use substring matching — "it's fine" matches inside longer text

## Thresholds

| Transition | Min evals | Agreement | Violation rate | Confidence p50 |
|------------|-----------|-----------|----------------|----------------|
| shadow → warn | ≥50 | ≥70% | 2%–40% | — |
| warn → block | ≥200 | ≥85% | 5%–30% | ≥0.7 |
| warn → shadow | any | <50% | >60% | — |
| delete candidate | shadow ≥14d | no improvement | — | — |
