---
agent: claude -p --model claude-sonnet-4-6 --allowedTools Read,Write,Bash,Skill --disallowedTools=mcp
commands:
  - name: existing_rules
    run: ls -la .vaudeville/rules/ 2>/dev/null || echo "empty"
  - name: session_patterns
    run: ./commands/generate/session-analytics.sh
args: [instructions, p_min, r_min, f1_min, mode]
---

# vaudeville generate (designer)

You are a rule-creation Designer. You run ONCE. Produce 3 new vaudeville rule YAMLs at `.vaudeville/rules/<name>.yaml`, each with `tier: shadow` (or `tier: warn` if `--mode live`), based on the instructions below and any analytics patterns found.

Do NOT tune. Do NOT eval. The orchestrator handles evaluation and routes each rule through the tune pipeline.

**Instructions**: {{ args.instructions }}

**Existing rules**:
{{ commands.existing_rules }}

**Session patterns** (if available):
{{ commands.session_patterns }}

## Rule YAML format

```yaml
name: my-detector              # Unique identifier (kebab-case)
event: Stop                    # Hook event: Stop, PostToolUse, PreToolUse, UserPromptSubmit
prompt: |
  Classify this text as "violation" or "clean".

  VIOLATION if:
  - <condition 1>

  CLEAN if:
  - <condition 1>

  Response: "<example>"
  VERDICT: violation
  REASON: <why>

  Now classify:
  {text}

  VERDICT: violation or clean
  REASON: one sentence
context:
  - field: last_assistant_message
action: block
message: "Quality violation: {reason}"
threshold: 0.5
tier: shadow
test_cases:
  - text: "Example violation text"
    label: violation
  - text: "Example clean text"
    label: clean
```

Write all 3 rule files. Do not print to stdout — the orchestrator reads the files directly.
