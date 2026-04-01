---
name: hook-suggester
description: >
  Analyze Claude Code session history to suggest hooks tailored to the user's
  actual usage patterns. Mines session-analytics data for dangerous commands,
  tool misuse, high error rates, permission friction, missing quality gates,
  broken hooks, and repeated automatable patterns — then generates concrete
  hook implementations via hook-creator. Use when the user asks "what hooks
  should I add", "suggest hooks", "analyze my usage for hooks", "what should
  I enforce", "improve my hooks", "hook suggestions", or wants to discover
  enforcement opportunities they haven't thought of. Also trigger for
  "vaudeville suggestions", "hook recommendations", "what am I doing wrong",
  "what hooks do I need", "hook audit", "analyze my hooks", "optimize my
  workflow", "what's failing in my sessions", "guard against", or any question
  about optimizing their Claude Code workflow through hooks. Do NOT use for
  creating a specific known hook (use hook-creator), querying raw session data
  (use session-analytics), or general workflow questions.
model: sonnet
context: fork
allowed-tools: Bash(python3:*), Bash(duckdb:*), Read, Skill
---

# hook-suggester

Analyze session history → find patterns → suggest hooks → implement them.

This skill bridges session-analytics (the data) and hook-creator (the builder).
It answers: "What hooks would actually help me, based on how I work?"

## Workflow

### Step 1: Ensure fresh analytics data

Run the session-analytics ingestion script first. It has a 1-hour TTL cache.

```bash
python3 <skill-dir>/../session-analytics/scripts/ingest.py
```

### Step 2: Run the analysis

```bash
python3 <skill-dir>/scripts/analyze.py [--days 14] [--min-occurrences 3]
```

Options:
- `--days N` — Look back N days (default: 14)
- `--min-occurrences N` — Minimum pattern count to surface (default: 3)
- `--json` — Output structured JSON instead of human-readable text

The analyzer checks 8 pattern categories:

| Pattern | Event | What it catches |
|---------|-------|----------------|
| Dangerous bash | PreToolUse | rm -rf, force push, DROP, --no-verify |
| Tool misuse | PreToolUse | Using bash for grep/find/cat/sed instead of dedicated tools |
| High error tools | PostToolUse | Tools with >20% error rate |
| Permission friction | PreToolUse | Frequently denied tool calls |
| Missing quality hooks | Stop | Low ratio of hook-checked stops |
| Hook failures | Stop | Hooks that error and silently pass |
| Code write volume | PostToolUse | Code write events counted by language |
| Repeated commands | SessionStart | Bash commands repeated many times |

### Step 3: Present findings and triage

Present results as a numbered list. For each suggestion:

```
N. [PRIORITY] Title
   Event: X | Type: Y
   Finding: <one sentence>
   Examples: <top 3 from data>
```

End with: "Which suggestions would you like me to implement? (all / numbers / none)"

The user may want all, some, or none.

### Step 4: Implement selected hooks

For each suggestion the user approves, invoke the appropriate skill.

**For SLM-powered rules** (natural language, intent detection, context-aware):

```
Skill(skill: "vaudeville:add-rule", args: "<description of what to detect>")
```

This creates a vaudeville YAML rule in `rules/`, writes test cases in `tests/`,
registers it in `hooks/hooks.json`, and runs eval to verify accuracy. Use this
when the suggestion involves understanding meaning — hedging, dismissal,
sycophancy, incomplete work, deferral, etc.

**For simple pattern-matching hooks** (structural, regex, deterministic):

```
Skill(skill: "hook-creator", args: "<description of the hook to create>")
```

This creates a JS, Python, or bash script and registers it in settings.json
or hooks.json. Use this for file guards, command blockers, auto-formatters,
context injection, and other structural checks.

### Decision: SLM rule vs. simple hook

Use an **SLM rule** (vaudeville daemon) when:
- The check requires understanding intent or context
- Simple regex/pattern matching would have high false positives
- The content being checked is natural language (responses, PR comments)

Use a **simple hook** (JS/bash script) when:
- The check is structural (file paths, command patterns, JSON fields)
- A regex or string match is sufficient
- Speed matters (simple hooks are <100ms, SLM is 1-5s)

**Example — SLM rule**: "Detect when Claude claims work is done but left TODOs"
(requires understanding natural language intent, not just pattern matching)

**Example — Simple hook**: "Block `rm -rf /` in bash commands"
(exact regex match, no ambiguity, <1ms)

## Example Session

```
User: suggest some hooks for me

1. Run ingestion:
   python3 .../session-analytics/scripts/ingest.py

2. Run analysis:
   python3 .../hook-suggester/scripts/analyze.py

3. Output:
   [!!!] 1. Guard dangerous bash commands
         Event: PreToolUse | Type: safety
         Found 12 uses of dangerous patterns...

   [ ! ] 2. Redirect bash to dedicated tools
         Event: PreToolUse | Type: quality
         Found 45 bash calls that should use dedicated tools...

   [ . ] 3. Auto-format on file writes
         Event: PostToolUse | Type: workflow
         Found 230 code writes across 3 languages...

4. Ask user which to implement
5. Invoke hook-creator for each approved suggestion
```

## What This Skill Doesn't Do

- Create hooks without data backing (use hook-creator directly)
- Query arbitrary session analytics (use session-analytics)
- Modify existing hooks (use hook-creator for updates)
- Make decisions for the user — always present and let them choose

## Gotchas

- If the DuckDB database doesn't exist, analyze.py exits with an error — run
  ingestion first. The skill workflow already covers this but worth repeating.
- Stale data (>1 hour) may miss recent sessions. Use `--force` on ingestion
  if the user wants current-session data included.
- Tool misuse counts include legitimate subagent bash calls (agents using grep/find
  intentionally). High counts don't always mean the *user* should add a hook — they
  may reflect agent behavior that's already correct for the subagent's context.
- The analyzer runs 8 independent DuckDB queries. If the database is large (>100K
  entries), this can take 5-10 seconds. Don't run with --force unnecessarily.
- hook-creator is invoked per suggestion — if the user approves 5 hooks, that's
  5 sequential Skill invocations. Batch acknowledgment is fine but implementation
  is serial.
