# Hook Patterns Reference

Production-tested hook recipes organized by use case.

## Table of Contents
- [Quality Enforcement](#quality-enforcement)
- [Safety Guards](#safety-guards)
- [Context Management](#context-management)
- [Workflow Automation](#workflow-automation)
- [Plugin Hooks](#plugin-hooks)

---

## Quality Enforcement

### Stop Hook — Detect hedging language
Blocks responses that use uncertain language about untested code.

```python
#!/usr/bin/env python3
import json, sys

data = json.load(sys.stdin)
text = data.get("last_assistant_message", "")

hedging = ["should work", "might need to", "probably fine", "i think this"]
found = [h for h in hedging if h in text.lower()]

if found:
    print(json.dumps({
        "decision": "block",
        "reason": f"Hedging detected: {', '.join(found)}. Verify before claiming."
    }))
    sys.exit(2)
```

### Stop Hook — Detect premature completion
Catches responses that declare work "done" while leaving TODOs.

```python
#!/usr/bin/env python3
import json, sys, re

data = json.load(sys.stdin)
text = data.get("last_assistant_message", "")

completion_phrases = ["all done", "that should do it", "everything is working"]
todo_phrases = ["todo", "fixme", "will handle later", "in a follow-up"]

claims_done = any(p in text.lower() for p in completion_phrases)
has_todos = any(p in text.lower() for p in todo_phrases)

if claims_done and has_todos:
    print(json.dumps({
        "decision": "block",
        "reason": "Claims completion but contains unresolved TODOs"
    }))
    sys.exit(2)
```

### PostToolUse — Scan written files for banned patterns
Catches debug statements, hardcoded secrets, and lazy code.

```javascript
const fs = require('fs');
const toolInput = JSON.parse(process.env.TOOL_INPUT || '{}');
const filePath = toolInput.file_path || toolInput.path || '';

if (!filePath || !fs.existsSync(filePath)) process.exit(0);

// Skip non-code files
const skip = ['.md', '.json', '.yaml', '.yml', '.toml', '.lock', '.txt'];
if (skip.some(ext => filePath.endsWith(ext))) process.exit(0);

const content = fs.readFileSync(filePath, 'utf-8');
const violations = [];

// Ellipsis / lazy code
if (/\.{3}\s*(existing|rest|remaining|other)/i.test(content)) {
  violations.push('Contains ellipsis placeholder — write the actual code');
}

// Hardcoded secrets
if (/(?:password|secret|api_key|token)\s*=\s*["'][^"']{8,}/i.test(content)) {
  violations.push('Possible hardcoded secret detected');
}

// Debug prints in production code
if (!filePath.includes('test') && /console\.log\(|print\(.*debug/i.test(content)) {
  violations.push('Debug statement in non-test file');
}

if (violations.length > 0) {
  console.log(`Issues in ${filePath}:\n${violations.map(v => `  - ${v}`).join('\n')}`);
}
```

---

## Safety Guards

### PreToolUse — Bash command guard
Prevents dangerous shell commands and suggests safer alternatives.

```javascript
const toolName = process.env.TOOL_NAME || '';
if (toolName !== 'Bash') process.exit(0);

const toolInput = JSON.parse(process.env.TOOL_INPUT || '{}');
const cmd = toolInput.command || '';

const blocked = [
  { pattern: /rm\s+-rf\s+[\/~]/, msg: 'Refusing to rm -rf root or home' },
  { pattern: /git\s+push\s+.*--force(?!\s+--with-lease)/, msg: 'Use --force-with-lease instead of --force' },
  { pattern: /DROP\s+(?:TABLE|DATABASE)/i, msg: 'Refusing to drop database objects' },
  { pattern: />\s*\/dev\/sd[a-z]/, msg: 'Refusing to write to block devices' },
];

for (const rule of blocked) {
  if (rule.pattern.test(cmd)) {
    console.log(`BLOCKED: ${rule.msg}`);
    process.exit(2);
  }
}
```

### PreToolUse — Write guard for sensitive paths
Prevents writes to critical config files without confirmation.

```javascript
const toolName = process.env.TOOL_NAME || '';
if (!['Edit', 'Write'].includes(toolName)) process.exit(0);

const toolInput = JSON.parse(process.env.TOOL_INPUT || '{}');
const filePath = toolInput.file_path || '';

const sensitive = ['.env', 'credentials', 'secrets', '.pem', '.key', 'id_rsa'];
if (sensitive.some(s => filePath.includes(s))) {
  console.log(`WARNING: Modifying sensitive file: ${filePath}`);
}
```

---

## Context Management

### UserPromptSubmit — Force skill evaluation
Reminds Claude to check installed skills before responding.

```javascript
const prompt = process.env.USER_PROMPT || '';
const keywords = ['review', 'test', 'deploy', 'analyze', 'refactor',
                   'migrate', 'audit', 'benchmark', 'profile', 'debug'];
if (keywords.some(kw => prompt.toLowerCase().includes(kw))) {
  console.log('MANDATORY: Evaluate installed skills before proceeding.');
}
```

### UserPromptSubmit — Session end detector
Detects goodbye phrases and reminds Claude to wrap up cleanly.

```bash
#!/bin/bash
PROMPT="${USER_PROMPT:-}"
if echo "$PROMPT" | grep -qiE '\b(bye|goodbye|done for the day|signing off|good night)\b'; then
  echo "User is ending the session. Wrap up current work cleanly."
fi
```

---

## Workflow Automation

### SessionStart — Spawn background daemon
Starts a long-running service at session start.

```bash
#!/bin/bash
set -euo pipefail

SOCKET="/tmp/my-daemon-${CLAUDE_SESSION_ID}.sock"

# Skip if already running
if [ -S "$SOCKET" ]; then
  echo "Daemon already running"
  exit 0
fi

nohup python3 -m my_daemon --socket "$SOCKET" > /dev/null 2>&1 &
echo "Daemon started on $SOCKET"
```

### PostToolUse — Auto-format on write
Runs formatter after file writes.

```javascript
const { execSync } = require('child_process');
const toolInput = JSON.parse(process.env.TOOL_INPUT || '{}');
const filePath = toolInput.file_path || '';

if (!filePath) process.exit(0);

const formatters = {
  '.py': `ruff format "${filePath}"`,
  '.ts': `prettier --write "${filePath}"`,
  '.tsx': `prettier --write "${filePath}"`,
  '.js': `prettier --write "${filePath}"`,
  '.rs': `rustfmt "${filePath}"`,
};

const ext = filePath.slice(filePath.lastIndexOf('.'));
const cmd = formatters[ext];
if (cmd) {
  try {
    execSync(cmd, { stdio: 'pipe' });
  } catch {
    // Formatter not available — skip silently
  }
}
```

---

## Plugin Hooks

### Plugin hooks.json structure
Plugins define hooks in `hooks/hooks.json` at the plugin root:

```json
{
  "description": "My plugin quality gates",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/checker.py",
            "timeout": 15,
            "statusMessage": "Running quality check..."
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "node ${CLAUDE_PLUGIN_ROOT}/hooks/write-validator.js",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**Plugin-specific notes:**
- Use `${CLAUDE_PLUGIN_ROOT}` for paths relative to plugin root
- `statusMessage` shows in the UI while the hook runs
- Plugin hooks merge with user hooks — both fire
- Plugin hooks cannot override user hooks (additive only)
