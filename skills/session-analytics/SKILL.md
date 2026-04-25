---
name: vaudeville:session-analytics
description: >
  Query Claude Code's own JSONL session logs via DuckDB for usage analytics,
  tool patterns, error forensics, hook impact analysis, and routing decisions.
  Use when the user asks about their Claude usage patterns, tool frequencies,
  error rates, permission denials, agent routing, skill invocations, MCP server
  usage, session timelines, hook effectiveness, or any question about "how has
  Claude been working". Also trigger when the user says "session analytics",
  "query my logs", "tool usage", "how often do I use", "check my sessions",
  "analyze my usage", "hook stats", "what hooks fired", or asks about specific
  tool/agent/skill/hook behavior across sessions. Also trigger for "vaudeville
  hook analytics", "hook effectiveness", "hook firing rate", "enforcement stats",
  or any question about how vaudeville hooks are performing. This skill turns
  raw JSONL into a queryable DuckDB database — use it instead of writing ad-hoc
  Python scripts to parse logs. Do NOT use for debugging current code issues,
  reading individual session transcripts, or questions about Claude's
  capabilities — this skill is for aggregate usage analytics across historical
  sessions.
model: sonnet
allowed-tools: Bash(duckdb:*), Bash(python3:*), Read
---

# session-analytics

Query Claude Code session logs. DuckDB is the database, SQL is the interface.

## How it works

Session logs live as JSONL files in `~/.claude/projects/`. Each conversation
turn is one JSON line. This skill materializes them into a DuckDB database
with pre-flattened tables so you can answer questions with single SQL queries.

## Prerequisites

Requires the `duckdb` CLI on PATH (`brew install duckdb` on macOS). The ingest
script shells out to duckdb — it will error if the binary isn't found. Never
use python3 for querying — always use the duckdb CLI directly.

## Step 1: Ensure the database exists

Run the ingestion script. It has a 1-hour TTL — it skips work if the database
is fresh. Always run this first.

```bash
python3 <skill-dir>/scripts/ingest.py
```

Use `--force` to re-ingest regardless of TTL (e.g., if the user wants the
latest data from the current session).

The database lives at `~/.claude/analytics/sessions.duckdb`.

## Step 2: Query via DuckDB CLI

All queries go through the CLI. Use `-json` for structured output you can
reason about, or omit it for human-readable tables.

```bash
duckdb ~/.claude/analytics/sessions.duckdb -json -c "SELECT ..."
```

## Schema

### `tool_uses`
Flattened from assistant message `content[]` blocks where `type = 'tool_use'`.

| Column | Type | Description |
|--------|------|-------------|
| tool_name | VARCHAR | Tool invoked (Bash, Read, Edit, Agent, Skill, mcp__*) |
| tool_use_id | VARCHAR | Unique ID for joining with tool_results |
| input | JSON | Full input object |
| bash_cmd | VARCHAR | Extracted command (Bash only) |
| skill_name | VARCHAR | Extracted skill (Skill only) |
| skill_args | VARCHAR | Extracted args (Skill only) |
| agent_type | VARCHAR | Extracted subagent_type (Agent only) |
| agent_desc | VARCHAR | Extracted description (Agent only) |
| agent_mode | VARCHAR | Extracted mode (Agent only) |
| grep_pattern | VARCHAR | Extracted pattern (Grep only) |
| file_path | VARCHAR | Extracted file_path (Read/Edit/Write) |
| query | VARCHAR | Extracted query (ToolSearch) |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |
| cwd | VARCHAR | Working directory |
| gitBranch | VARCHAR | Git branch at time of call |

### `tool_results`
Flattened from user message `content[]` blocks where `type = 'tool_result'`.

| Column | Type | Description |
|--------|------|-------------|
| tool_use_id | VARCHAR | Matches tool_uses.tool_use_id |
| content | VARCHAR | Result text (truncated to 500 chars) |
| is_error | VARCHAR | 'true' if the tool call failed |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |

### `stop_events`
Assistant messages where Claude stopped generating.

| Column | Type | Description |
|--------|------|-------------|
| stop_reason | VARCHAR | end_turn, stop_sequence, or max_tokens |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |
| cwd | VARCHAR | Working directory |
| gitBranch | VARCHAR | Git branch |

### `agent_spawns`
Subset of tool_uses for Agent calls.

| Column | Type | Description |
|--------|------|-------------|
| agent_type | VARCHAR | Subagent type (defaults to 'general-purpose') |
| description | VARCHAR | Task description |
| mode | VARCHAR | Permission mode |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |
| cwd | VARCHAR | Working directory |

### `skill_invocations`
Subset of tool_uses for Skill calls.

| Column | Type | Description |
|--------|------|-------------|
| skill_name | VARCHAR | Which skill was invoked |
| args | VARCHAR | Arguments passed |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |
| cwd | VARCHAR | Working directory |

### `mcp_calls`
Subset of tool_uses for MCP server calls (tool_name starts with `mcp__`).

Same columns as `tool_uses`. The tool_name encodes the server and method:
`mcp__<server>__<method>` (e.g., `mcp__context7__query-docs`).

### `sessions`
One row per unique (sessionId, cwd, branch) combination.

| Column | Type | Description |
|--------|------|-------------|
| sessionId | VARCHAR | Session identifier |
| first_seen | VARCHAR | Earliest timestamp |
| last_seen | VARCHAR | Latest timestamp |
| project | VARCHAR | Working directory |
| branch | VARCHAR | Git branch |
| entry_count | INTEGER | Total JSONL entries in session |

### `stop_hooks`
System entries with subtype `stop_hook_summary`.

| Column | Type | Description |
|--------|------|-------------|
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |
| hookCount | INTEGER | Number of hooks that ran |
| hookInfos | JSON | Array of {command, durationMs} |
| hookErrors | JSON | Array of error strings |
| preventedContinuation | BOOLEAN | Whether the hook blocked Claude |
| stopReason | VARCHAR | Reason text |
| hasOutput | BOOLEAN | Whether hook produced output |
| level | VARCHAR | suggestion, warning, etc. |

### `permission_denials`
Pre-filtered tool_results for permission-related failures.

| Column | Type | Description |
|--------|------|-------------|
| content | VARCHAR | The denial message |
| sessionId | VARCHAR | Session identifier |
| timestamp | VARCHAR | ISO timestamp |

### `raw_entries`
The full unflattened JSONL data. Use only when the materialized tables
don't have what you need.

## Query Catalog

Organized by investigation type. Use as starting points — modify for your question.

### Tool usage frequency
```sql
SELECT tool_name, count(*) AS uses
FROM tool_uses
GROUP BY tool_name
ORDER BY uses DESC;
```

### Error rate by tool
```sql
SELECT
    tu.tool_name,
    count(*) AS total,
    sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END) AS errors,
    round(sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS error_pct
FROM tool_uses tu
JOIN tool_results tr ON tu.tool_use_id = tr.tool_use_id
GROUP BY tu.tool_name
ORDER BY errors DESC;
```

### MCP server usage breakdown
```sql
SELECT
    split_part(tool_name, '__', 2) AS server,
    split_part(tool_name, '__', 3) AS method,
    count(*) AS calls
FROM mcp_calls
GROUP BY server, method
ORDER BY calls DESC;
```

### Skill usage over time
```sql
SELECT
    skill_name,
    timestamp::DATE AS day,
    count(*) AS uses
FROM skill_invocations
GROUP BY skill_name, day
ORDER BY day DESC, uses DESC;
```

### Busiest sessions
```sql
SELECT
    s.sessionId,
    s.project,
    s.branch,
    s.first_seen,
    s.last_seen,
    (SELECT count(*) FROM tool_uses tu WHERE tu.sessionId = s.sessionId) AS tool_calls
FROM sessions s
ORDER BY tool_calls DESC
LIMIT 10;
```

### Most common Bash commands
```sql
SELECT
    substr(bash_cmd, 1, 80) AS cmd,
    count(*) AS uses
FROM tool_uses
WHERE tool_name = 'Bash' AND bash_cmd IS NOT NULL
GROUP BY cmd
ORDER BY uses DESC
LIMIT 20;
```

### Permission friction audit
```sql
SELECT
    CASE
        WHEN bash_cmd LIKE '%python3%' THEN 'python3 inline'
        WHEN bash_cmd LIKE '%cat %' AND bash_cmd LIKE '%>%' THEN 'cat redirect (use Write)'
        WHEN bash_cmd LIKE 'find %' OR bash_cmd LIKE '% find %' THEN 'find (use Glob)'
        WHEN bash_cmd LIKE 'grep %' OR bash_cmd LIKE 'egrep %' THEN 'grep (use Grep)'
        WHEN bash_cmd LIKE 'sed %' OR bash_cmd LIKE '%sed -i%' THEN 'sed (use Edit)'
        WHEN bash_cmd LIKE 'cd %' AND bash_cmd LIKE '%git%' THEN 'cd+git compound'
        WHEN bash_cmd LIKE 'cd %' AND bash_cmd LIKE '%gh %' THEN 'cd+gh compound'
        ELSE 'other: ' || substr(bash_cmd, 1, 50)
    END AS category,
    count(*) AS denials
FROM tool_uses tu
JOIN tool_results tr ON tu.tool_use_id = tr.tool_use_id
WHERE tu.tool_name = 'Bash'
AND tr.is_error = 'true'
AND tr.content LIKE 'Permission to use Bash%'
GROUP BY category
ORDER BY denials DESC;
```

### Hook impact analysis
```sql
SELECT
    level,
    preventedContinuation,
    substr(stopReason, 1, 100) AS reason,
    count(*) AS cnt
FROM stop_hooks
GROUP BY level, preventedContinuation, reason
ORDER BY cnt DESC
LIMIT 20;
```

### Hook duration breakdown
```sql
SELECT
    json_extract_string(hook_info, '$.command') AS hook_command,
    count(*) AS executions,
    round(avg(CAST(json_extract_string(hook_info, '$.durationMs') AS DOUBLE)), 0) AS avg_ms,
    max(CAST(json_extract_string(hook_info, '$.durationMs') AS DOUBLE)) AS max_ms
FROM stop_hooks,
     unnest(json_extract(hookInfos, '$[*]')) AS t(hook_info)
WHERE hookInfos IS NOT NULL
GROUP BY hook_command
ORDER BY executions DESC;
```

### Agent spawn patterns by skill
```sql
SELECT
    si.skill_name,
    asp.agent_type,
    substr(asp.description, 1, 60) AS agent_desc,
    count(*) AS spawns
FROM skill_invocations si
JOIN agent_spawns asp
    ON si.sessionId = asp.sessionId
    AND asp.timestamp::TIMESTAMP BETWEEN si.timestamp::TIMESTAMP
        AND si.timestamp::TIMESTAMP + INTERVAL '10' MINUTE
GROUP BY si.skill_name, asp.agent_type, agent_desc
ORDER BY si.skill_name, spawns DESC;
```

### Daily activity heatmap
```sql
SELECT
    timestamp::DATE AS day,
    extract(hour FROM timestamp::TIMESTAMP) AS hour,
    count(*) AS calls
FROM tool_uses
WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '14' DAY
GROUP BY day, hour
ORDER BY day DESC, hour;
```

### Filter by project cwd
Restrict any query to a single project by matching `cwd LIKE '%<keyword>%'`.
Substitute `<keyword>` with a fragment of the project's working directory
(e.g. `vaudeville`, `port-louis`). This is the same pattern
`vaudeville.analytics.query_session_patterns(project_filter=...)` uses.

```sql
-- Top bash commands scoped to one project
SELECT bash_cmd, count(*) AS uses
FROM tool_uses
WHERE tool_name = 'Bash'
  AND bash_cmd IS NOT NULL
  AND cwd LIKE '%vaudeville%'
GROUP BY bash_cmd
ORDER BY uses DESC
LIMIT 10;

-- Top tool uses scoped to one project
SELECT tool_name, count(*) AS uses
FROM tool_uses
WHERE cwd LIKE '%vaudeville%'
GROUP BY tool_name
ORDER BY uses DESC
LIMIT 10;

-- Permission denials scoped to one project (join through sessions.project)
SELECT pd.content, count(*) AS cnt
FROM permission_denials pd
JOIN sessions s ON pd.sessionId = s.sessionId
WHERE s.project LIKE '%vaudeville%'
GROUP BY pd.content
ORDER BY cnt DESC
LIMIT 5;
```

## Pre-built query scripts

For common analytics questions, use these scripts instead of writing inline SQL
or Python. All scripts live in `<skill-dir>/scripts/queries/`. Most accept
`--days N`, `--limit N`, and `--json` flags; `hook_stats.py` accepts `--days`
and `--json` but uses a fixed error-pattern limit of 10.

```bash
# Denied tool names ranked by frequency (replaces inline Python regex extraction)
python3 <skill-dir>/scripts/queries/denied_tools.py [--days 14] [--json]

# Top tools by usage count
python3 <skill-dir>/scripts/queries/tool_usage.py [--days 14] [--json]

# Tools ranked by error rate
python3 <skill-dir>/scripts/queries/error_rates.py [--days 14] [--min-uses 5] [--json]

# Most-repeated bash commands (add --dangerous for risky ones only)
python3 <skill-dir>/scripts/queries/bash_patterns.py [--days 14] [--dangerous] [--json]

# Bash commands that should use dedicated tools (cat→Read, grep→Grep, etc.)
python3 <skill-dir>/scripts/queries/tool_misuse.py [--days 14] [--json]

# Hook coverage ratio and error patterns
python3 <skill-dir>/scripts/queries/hook_stats.py [--days 14] [--json]
```

Use `--json` for structured output you can pipe to `jq`. Without it, most
scripts output tab-separated values; `hook_stats.py` prints a multi-line
human-formatted summary instead.

## Query patterns

When answering user questions:

1. Run `python3 <skill-dir>/scripts/ingest.py` first (fast if cached)
2. Translate the question into SQL against the schema above
3. Use `duckdb ~/.claude/analytics/sessions.duckdb -json -c "..."` to execute
4. Present results as markdown tables when there are 3+ rows. For single-value
   answers, state the number directly. Lead with the answer, then show the
   query if the user might want to modify it.
5. For follow-up questions, skip ingestion — the database persists

For complex analysis, chain multiple queries. Aim to answer within 5-8 queries.
If the question needs more, present intermediate findings and ask if deeper
analysis is needed.

When filtering by project, use `LIKE '%keyword%'` on the `cwd` column since
full paths are verbose.

Timestamps are ISO 8601 strings stored as VARCHAR. Cast to TIMESTAMP for
date math: `timestamp::TIMESTAMP`, `timestamp::DATE`, or use `extract()`.

## Gotchas

- Empty DuckDB CLI JSON results return `[{]` not `[]` — handle both
- `is_error` in tool_results is VARCHAR `'true'`/`'false'`, not boolean
- Timestamps are VARCHAR — cast explicitly for date math
- The `input` column in tool_uses is full JSON — use `json_extract_string()`
  to pull specific fields not already materialized as columns
- DuckDB CLI `-json` mode returns all values as strings

## What This Skill Doesn't Do

- Read or reconstruct individual session transcripts
- Debug issues in the current session
- Modify or delete session log files
- Query external data sources
- Write ad-hoc Python scripts — all queries go through duckdb CLI
