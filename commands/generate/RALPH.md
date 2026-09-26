---
agent: claude -p --bare --dangerously-skip-permissions --strict-mcp-config --mcp-config '{"mcpServers":{}}' --settings '{"apiKeyHelper":"./scripts/claude-oauth-keyhelper.sh"}' --model claude-sonnet-4-6 --allowedTools Read,Write,Bash,Skill
commands:
  - name: existing_rules
    run: ./list-rules.sh
  - name: session_patterns
    run: ./session-analytics.sh
args: [instructions, p_min, r_min, f1_min, mode, rules_dir]
---

# vaudeville generate (designer)

You are a rule-creation Designer. You run ONCE. Produce 3 new vaudeville rule YAMLs at `{{ args.rules_dir }}/<name>.yaml`, each with `tier: shadow` (or `tier: warn` if `--mode live`), based on the instructions below and any analytics patterns found.

Do NOT tune. Do NOT eval. The orchestrator handles evaluation and routes each rule through the tune pipeline.

**Instructions**: {{ args.instructions }}

**Existing rules**:
{{ commands.existing_rules }}

**Session patterns** (if available):
{{ commands.session_patterns }}

## Rule YAML format

```yaml
type: decide                   # decide (this generator only writes decide rules)
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

  VERDICT: violation or clean
  REASON: one sentence
outcomes: [violation, clean]   # labels the model may return
"on":
  violation: warn               # or block if --mode live
tier: shadow                    # or warn if --mode live
test_cases:
  - text: "Example violation text"
    outcome: violation
  - text: "Example clean text"
    outcome: clean
```

Write all 3 rule files. Do not print to stdout — the orchestrator reads the files directly.
