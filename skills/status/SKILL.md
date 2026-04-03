---
name: status
description: >
  Check vaudeville daemon health — socket alive, model loaded, rules count,
  hook wiring, and recent verdicts. Use when the user asks "is vaudeville
  running", "daemon status", "check hooks", "why isn't my hook firing",
  "vaudeville health", "is the model loaded", "what rules are active",
  "check daemon", "health check", "is the daemon up", "status", or any
  question about whether vaudeville is working correctly. Also trigger when
  troubleshooting hook failures — a dead daemon is the most common cause.
model: sonnet
allowed-tools: Bash, Read, Glob
---

# status

Quick diagnostic of the vaudeville system. Answers: "Is vaudeville working,
and what's it configured to do?"

## What to Check

Run these checks in order and report all results in a single summary.

### 1. Daemon Process

Look for active daemons by scanning PID files:

```bash
for pid_file in /tmp/vaudeville-*.pid; do
  [ -f "$pid_file" ] || continue
  session_id=$(basename "$pid_file" | sed 's/^vaudeville-//;s/\.pid$//')
  pid=$(cat "$pid_file" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    socket="/tmp/vaudeville-${session_id}.sock"
    socket_ok="no"
    [ -S "$socket" ] && socket_ok="yes"
    echo "RUNNING  session=${session_id}  pid=${pid}  socket_exists=${socket_ok}"
  else
    echo "STALE    session=${session_id}  pid=${pid}  (process dead, PID file leftover)"
  fi
done
```

If no PID files exist at all, report: "No daemon found. Start a Claude Code
session with vaudeville hooks installed to spawn one."

### 2. Rules Loaded

List YAML rule files in the plugin's `rules/` directory:

```bash
ls ${CLAUDE_PLUGIN_ROOT}/rules/*.yaml 2>/dev/null
```

For each rule, read the `name` and `event` fields from the YAML frontmatter
to show what each rule detects and when it fires. Use the first two lines
(`name:` and `event:`) — no need to parse the full file.

### 3. Hook Wiring

Read `${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json` and summarize which lifecycle
events have hooks registered. For each event (SessionStart, Stop,
PreToolUse, PostToolUse), list the commands and any matchers. The goal is
to confirm that the rules from step 2 are actually wired into hooks.

Flag any rule that exists in `rules/` but is NOT referenced in any hook
command — that rule is defined but will never fire.

### 4. Daemon Logs (if running)

If at least one daemon is running, show the last 20 lines of its log:

```bash
tail -20 /tmp/vaudeville-${session_id}.log
```

If multiple daemons are running, show the most recently modified log only.

## Output Format

Present results as a compact status report:

```
## Vaudeville Status

**Daemon**: [RUNNING | DOWN | STALE]
- Session: <id> | PID: <pid> | Socket: [ok | missing]

**Rules** (N loaded):
- violation-detector (Stop)
- dismissal-detector (Stop)
- deferral-detector (PostToolUse)

**Hooks** (from hooks.json):
- SessionStart: session-start.sh (daemon launcher)
- Stop: runner.py -> violation-detector, dismissal-detector
- PostToolUse[PR tools]: runner.py -> deferral-detector

**Unwired rules**: none | <list>

**Recent log** (last 20 lines):
<log tail>
```

Adapt the template to fit what you actually find — don't print sections
that have no data (e.g., skip "Recent log" if the daemon is down).

## Troubleshooting Hints

When reporting results, include actionable guidance for common issues:

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No PID files | Daemon never started | Check that `hooks/hooks.json` SessionStart hook is registered |
| STALE PID | Daemon crashed | Check log for errors, restart session |
| Socket missing | Startup race or crash | Restart session; if persistent, check disk space in /tmp |
| Rule not wired | Rule file exists but not in hooks.json | Add it to the appropriate hook command in hooks.json |
| Empty rules/ | No rules defined | Use `/vaudeville:add-hook` to create one |
