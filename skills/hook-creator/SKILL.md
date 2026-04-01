---
name: hook-creator
description: >
  Create, validate, and debug Claude Code hook configurations. Use when the user
  wants to create a new hook, fix a broken hook, understand why a hook isn't
  firing, add enforcement to their workflow, or set up quality gates. Also
  trigger when the user says "create a hook", "add a hook", "hook not working",
  "why isn't my hook firing", "validate my hooks", "hooks.json", "PreToolUse",
  "PostToolUse", "Stop hook", "SessionStart hook", "UserPromptSubmit hook", or
  asks about hook events, matchers, environment variables, or exit codes. Also
  trigger when the user wants to "enforce", "add a quality gate", "guard
  against", "block", or "prevent" specific behaviors — these are hook use cases
  even when the user doesn't mention hooks explicitly. This skill knows the full
  Claude Code hooks schema, all event types, available environment variables,
  and common patterns — use it instead of guessing at hook configuration. Do NOT
  use for general Claude Code configuration, MCP setup, or skill creation —
  this skill is specifically for hooks.
model: sonnet
allowed-tools: Bash(node:*), Bash(python3:*), Bash(jq:*), Read, Edit, Write, Glob, Grep
---

# hook-creator

Create and validate Claude Code hooks. Hooks are runtime enforcement — they
cannot be overridden by the model, unlike instructions in SKILL.md or CLAUDE.md.

## Hook Locations

Hooks can be defined in three places (all merged, all active):

| Location | Scope | File |
|----------|-------|------|
| User global | All projects | `~/.claude/settings.json` |
| Project shared | This repo (committed) | `.claude/settings.json` |
| Project local | This repo (gitignored) | `.claude/settings.local.json` |
| Plugin | Plugin users | `hooks/hooks.json` (in plugin root) |

For user hooks, they go in the `hooks` key of the settings file.
For plugin hooks, they go in `hooks/hooks.json` with a wrapper structure.

## Hook Events

| Event | When it fires | Common use |
|-------|--------------|------------|
| `SessionStart` | New session begins | Spawn daemons, set up state |
| `Stop` | Claude finishes a response | Quality checks on output |
| `PreToolUse` | Before a tool executes | Block dangerous operations |
| `PostToolUse` | After a tool executes | Validate outputs, scan files |
| `UserPromptSubmit` | User sends a message | Inject context, force evaluation |
| `Notification` | Subagent completes | React to background work |

## Settings.json Hook Format

```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "optional_tool_name_regex",
        "hooks": [
          {
            "type": "command",
            "command": "node .claude/hooks/my-hook.js",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

**Key fields:**
- `matcher` — Regex against tool name (only for PreToolUse/PostToolUse). Omit for other events.
- `type` — Always `"command"`
- `command` — Shell command to run. Working directory is the project root.
- `timeout` — Seconds before the hook is killed (default: 10, max: 60 for most, 120 for SessionStart)

## Plugin hooks.json Format

Plugins use a slightly different wrapper:

```json
{
  "description": "What these hooks do",
  "hooks": {
    "EventName": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/hooks/my-hook.sh",
            "timeout": 30,
            "statusMessage": "Running quality check..."
          }
        ]
      }
    ]
  }
}
```

Plugin hooks use `${CLAUDE_PLUGIN_ROOT}` to reference files relative to the plugin.

## Environment Variables Available to Hooks

All hooks receive:
- `CLAUDE_SESSION_ID` — Current session identifier
- `CLAUDE_PROJECT_DIR` — Project working directory

**PreToolUse / PostToolUse additionally receive:**
- `TOOL_NAME` — Name of the tool (e.g., "Bash", "Edit", "mcp__github__create_pr")
- `TOOL_INPUT` — JSON string of the tool's input parameters

**UserPromptSubmit receives:**
- `USER_PROMPT` — The text the user typed

**Stop receives:**
- Standard input (stdin) contains JSON with the session context

## Hook Communication Protocol

**Exit codes:**
- `0` — Allow (hook passed)
- Non-zero — Block (hook failed, operation is prevented)

**Stdout** is injected into Claude's context as a system message. Use this to:
- Warn Claude about issues (exit 0 + print warning)
- Block with explanation (exit 2 + print reason)
- Inject context silently (exit 0 + print context)

**Stderr** is logged but not shown to Claude.

**Stdin** (Stop hooks only): JSON object with session data including
`last_assistant_message` and tool call history.

## Hook Response JSON (Advanced)

For fine-grained control, hooks can output JSON instead of plain text.
Note: this format requires recent Claude Code versions. For maximum
compatibility, prefer exit codes (0 = allow, non-zero = block) with plain
text stdout.

```json
{
  "decision": "block",
  "reason": "Quality violation detected",
  "systemMessage": "Message injected into Claude's context"
}
```

Valid decisions: `"allow"`, `"block"`, `"warn"`.

## Creating a Hook — Step by Step

### 1. Determine the right event

Ask these questions:
- **Want to check Claude's output?** → `Stop`
- **Want to prevent a tool from running?** → `PreToolUse`
- **Want to validate what a tool produced?** → `PostToolUse`
- **Want to inject context when the user types?** → `UserPromptSubmit`
- **Want to set up state at session start?** → `SessionStart`

### 2. Choose the language

- **JavaScript (node)** — Best for most hooks. Fast startup, good for JSON parsing, file system access.
- **Python** — Good for complex logic, NLP, or when you need pip packages.
- **Bash** — Good for simple checks, spawning processes, or chaining CLI tools.

### 3. Write the hook script

Place hook scripts in `.claude/hooks/` for user hooks or `hooks/` for plugin hooks.

**Template — JavaScript PreToolUse guard:**
```javascript
// .claude/hooks/guard-name.js
const toolName = process.env.TOOL_NAME || '';
const toolInput = JSON.parse(process.env.TOOL_INPUT || '{}');

// Only check specific tools
if (toolName !== 'Bash') process.exit(0);

const cmd = toolInput.command || '';

// Check for banned patterns
if (/rm\s+-rf\s+\//.test(cmd)) {
  console.log('BLOCKED: Refusing to rm -rf root');
  process.exit(2);
}
```

**Template — JavaScript PostToolUse validator:**
```javascript
// .claude/hooks/validate-output.js
const fs = require('fs');
const toolInput = JSON.parse(process.env.TOOL_INPUT || '{}');
const filePath = toolInput.file_path || toolInput.path || '';

if (!filePath || !fs.existsSync(filePath)) process.exit(0);

const content = fs.readFileSync(filePath, 'utf-8');

// Example: warn about console.log in production code
if (!filePath.includes('.test.') && /console\.log\(/.test(content)) {
  console.log(`WARNING: ${filePath} contains console.log — use the project logger`);
}
```

**Template — Python Stop hook:**
```python
#!/usr/bin/env python3
import json
import sys

data = json.load(sys.stdin)
text = data.get("last_assistant_message", "")

# Example: detect hedging language
hedging = ["should work", "might need", "probably"]
found = [h for h in hedging if h in text.lower()]

if found:
    print(json.dumps({
        "decision": "block",
        "reason": f"Hedging detected: {', '.join(found)}"
    }))
    sys.exit(2)
```

**Template — Bash SessionStart:**
```bash
#!/bin/bash
# Start a background service
nohup some-daemon --session "$CLAUDE_SESSION_ID" > /dev/null 2>&1 &
echo "Background service started"
```

### 4. Register the hook

Add to the appropriate settings file:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "node .claude/hooks/guard-name.js",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### 5. Validate the configuration

After writing, validate that:
1. The JSON is valid (use `jq` to check)
2. The script is executable (if bash/python)
3. The script exits cleanly on the happy path
4. The matcher regex matches intended tools
5. The timeout is appropriate (most hooks should be <5s)

## Validation Checklist

When validating a hook configuration, check these common issues:

- **JSON syntax** — Trailing commas, missing quotes, wrong nesting
- **Event name typo** — Must be exact: `PreToolUse`, not `preToolUse` or `pre_tool_use`
- **Matcher scope** — Too broad catches unintended tools, too narrow misses targets
- **Timeout too low** — Network calls or inference need 10-30s, not the default 10
- **Missing shebang** — Python/bash scripts need `#!/usr/bin/env python3` or `#!/bin/bash`
- **Not executable** — `chmod +x` for bash/python scripts
- **Wrong working directory** — Hook runs from project root, not hook file location
- **Stdin not read** — Stop hooks MUST read stdin or the pipe breaks
- **Silent failure** — Hook crashes but exits 0, so it appears to pass

## Common Patterns

### Forced skill evaluation
```javascript
// UserPromptSubmit — forces Claude to check skills before responding
const prompt = process.env.USER_PROMPT || '';
const keywords = ['review', 'test', 'deploy', 'analyze', 'refactor'];
if (keywords.some(kw => prompt.toLowerCase().includes(kw))) {
  console.log('Check installed skills before proceeding.');
}
```

### Tool-specific matcher examples
```json
{
  "PreToolUse": [
    {
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "node .claude/hooks/bash-guard.js" }]
    },
    {
      "matcher": "Edit|Write",
      "hooks": [{ "type": "command", "command": "node .claude/hooks/write-guard.js" }]
    },
    {
      "matcher": "mcp__plugin_github.*",
      "hooks": [{ "type": "command", "command": "node .claude/hooks/github-guard.js" }]
    }
  ]
}
```

### Debugging hooks

If a hook isn't firing:
1. Check the event name is spelled correctly (case-sensitive)
2. For PreToolUse/PostToolUse, verify the matcher regex matches the tool name
3. Add `console.error("hook fired")` to stderr to confirm execution
4. Check `~/.claude/logs/` for hook error output
5. Test the script standalone: `echo '{}' | node .claude/hooks/my-hook.js`
6. Use `vaudeville:session-analytics` to query `stop_hooks` table for hook execution history

## Expected Output

For each hook created, deliver:
1. The hook script file (written to the appropriate location)
2. The settings.json/hooks.json entry (written or shown for user to add)
3. A standalone test command to verify the hook works

## Vaudeville Rules (SLM-Powered Hooks)

Vaudeville provides a second type of enforcement: **YAML rules** that classify
text using a local SLM (Phi-3-mini) via the vaudeville daemon. These are more
powerful than JS/bash hooks for detecting intent, context, and natural language
patterns.

### When to suggest a vaudeville rule instead

| Scenario | Recommendation |
|----------|---------------|
| Detect hedging, deferral, or sycophancy | Vaudeville rule |
| Check if work is actually complete | Vaudeville rule |
| Classify PR reply quality | Vaudeville rule |
| Block `rm -rf /` in bash | JS/bash hook (this skill) |
| Auto-format files after write | JS hook (this skill) |
| Inject context on session start | Bash hook (this skill) |

### How to create a vaudeville rule

Use the **add-rule** skill (`/vaudeville:add-rule`). It handles:
1. Writing the rule YAML in `rules/`
2. Writing test cases in `tests/`
3. Registering in `hooks/hooks.json`
4. Running eval to verify accuracy

If the user describes behavior that requires understanding intent or context
(not just pattern matching), suggest: "This sounds like a good fit for a
vaudeville SLM rule. Want me to create one with `/vaudeville:add-rule`?"

### Quick reference — rule format

```yaml
name: my-detector
event: Stop
prompt: |
  Classify as "violation" or "clean".
  ...examples...
  {text}
  VERDICT: violation or clean
  REASON: one sentence
context:
  - field: last_assistant_message
labels: [violation, clean]
action: block
message: "Quality violation: {reason}"
```

See existing rules in `rules/` for full examples.

## What This Skill Doesn't Do

- Configure MCP servers or plugins
- Create skills (use /skill-creator)
- Create vaudeville YAML rules (use /vaudeville:add-rule)
- Modify Claude Code's core settings beyond hooks
- Debug hook script business logic (it validates config, not your logic)

## References

For advanced hook patterns and real-world examples, read:
- `references/hook-patterns.md` — Production hook recipes organized by use case
- `rules/` — Existing vaudeville YAML rules (violation-detector, dismissal-detector, deferral-detector)
