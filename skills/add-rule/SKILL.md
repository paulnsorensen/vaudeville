---
name: add-rule
description: >
  Author a new vaudeville YAML enforcement rule with test cases and validation.
  Use when the user wants to create a new SLM-based quality gate, add semantic
  enforcement, write a vaudeville rule, or asks "add a rule", "new rule",
  "create a detector", "enforce X with vaudeville", "SLM rule for X",
  "block when Claude does X", or describes behavior they want to catch that
  requires understanding intent or context (not just regex). Also trigger when
  the user wants to detect patterns in natural language output like hedging,
  dismissal, deferral, sycophancy, or scope reduction. Do NOT use for simple
  pattern-matching hooks (use hook-creator for JS/bash guards) or for querying
  session data (use session-analytics).
model: sonnet
allowed-tools: Read, Edit, Write, Glob, Grep, Bash(uv:*), Bash(python3:*)
---

# add-rule

Author vaudeville YAML enforcement rules — SLM-powered semantic classifiers
that run via the daemon and `runner.py` infrastructure.

## When to Use Vaudeville Rules (vs. JS/Bash Hooks)

| Use a **vaudeville rule** when | Use a **JS/bash hook** when |
|-------------------------------|----------------------------|
| Check requires understanding intent or context | Check is structural (file paths, command patterns) |
| Simple regex would have high false positives | A regex or string match is sufficient |
| Content is natural language (responses, PR comments) | Content is structured data (JSON, file paths) |
| Classification is nuanced (hedging vs. factual) | Classification is binary (pattern present or not) |
| Speed budget is 1-5s (SLM inference) | Speed budget is <100ms |

## Rule YAML Format

Every rule is a single YAML file in `rules/`. Here's the complete schema:

```yaml
name: my-detector              # Unique identifier (kebab-case, matches filename)
event: Stop                     # Hook event: Stop, PostToolUse, PreToolUse, UserPromptSubmit
prompt: |                       # SLM classification prompt
  Classify this text as "violation" or "clean".

  VIOLATION if:
  - <condition 1>
  - <condition 2>

  CLEAN if:
  - <condition 1>
  - <condition 2>

  Examples:

  Response: "<example text>"
  VERDICT: violation
  REASON: <why>

  Response: "<example text>"
  VERDICT: clean
  REASON: <why>

  Now classify:
  {text}

  VERDICT: violation or clean
  REASON: one sentence
context:                        # How to extract text from hook input
  - field: last_assistant_message    # Dot-notation path into hook JSON
labels: [violation, clean]      # Valid verdict labels (always exactly 2)
action: block                   # What to do on violation: block, warn, or log
message: "Quality violation: {reason}"  # Template with {reason} placeholder
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique rule name, kebab-case, matches filename without `.yaml` |
| `prompt` | string | SLM prompt with `{text}` placeholder (and optional `{context}`) |
| `context` | list | At least one entry with `field:` (dot-path) or `file:` (disk path) |
| `labels` | list | Exactly 2 labels — first is the violation label, second is clean |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `event` | (none) | Hook event this rule targets — informational, used by `hooks.json` |
| `action` | `block` | `block` = prevent, `warn` = allow + inject warning, `log` = stderr only |
| `message` | `{reason}` | User-facing message template, `{reason}` is replaced by SLM output |

### Context Field Paths

The `context[].field` value is a dot-notation path into the hook input JSON:

| Event | Available fields | Common choice |
|-------|-----------------|---------------|
| Stop | `last_assistant_message`, `tool_calls`, `session_id` | `last_assistant_message` |
| PostToolUse | `tool_name`, `tool_input.*`, `tool_result.*`, `session_id` | `tool_input.body` for PR replies |
| PreToolUse | `tool_name`, `tool_input.*`, `session_id` | `tool_input.command` for bash |
| UserPromptSubmit | `user_prompt`, `session_id` | `user_prompt` |

## Writing Good Prompts

The prompt is the heart of the rule. The SLM (Phi-3-mini) is small — be explicit.

### Prompt Structure

1. **Task statement** — One sentence defining the classification task
2. **VIOLATION conditions** — Exhaustive list of what triggers a violation
3. **CLEAN conditions** — Exhaustive list of what's acceptable
4. **Examples** — 4-8 labeled examples (balanced violation/clean)
5. **Classification request** — `{text}` placeholder + expected output format

### Prompt Guidelines

- **Be specific**: "uses words like 'should work', 'might', 'probably'" beats "uses uncertain language"
- **Include boundary cases**: Cases that look like violations but are clean (and vice versa)
- **Add escape hatches**: "Quoting anti-patterns in meta-discussion is CLEAN" prevents false positives
- **Use the exact labels**: The prompt must output the same labels as the `labels` field
- **End with the same format**: Always end with `VERDICT: <label>\nREASON: one sentence`
- **Keep it under ~800 tokens**: The SLM has limited context — prompt + input must fit

### Anti-Patterns to Avoid

- **Vague criteria**: "bad code quality" — the SLM can't evaluate this reliably
- **Too many conditions**: More than 6-8 bullet points dilutes accuracy
- **No examples**: The SLM needs concrete examples to calibrate
- **Unbalanced examples**: 8 violations and 1 clean case biases toward false positives
- **Overlapping labels**: Labels must be mutually exclusive

## Test Cases File

Every rule MUST have a corresponding test file in `tests/<rule-name>.yaml`:

```yaml
rule: my-detector        # Must match the rule name
cases:
  # Violations — describe what makes each one a violation
  - text: "This should work. I've made the changes."
    label: violation
  - text: "The tests might pass now."
    label: violation

  # Clean — describe what makes each one clean
  - text: "All 42 tests pass. Fixed the null pointer."
    label: clean
  - text: "Ran the full suite — 0 failures."
    label: clean
```

### Test Case Guidelines

- **Minimum 10 cases**: At least 5 violation + 5 clean
- **Balance labels**: Roughly 50/50 split prevents bias
- **Include edge cases**: Boundary examples that test the prompt's precision
- **Use realistic text**: Copy real assistant output, not toy examples
- **Vary length**: Short (1 sentence) and long (paragraph) cases both matter

## Registration in hooks.json

After creating the rule and test file, register it in `hooks/hooks.json`:

### For Stop rules

Add the rule name to the existing Stop runner command:

```json
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/runner.py violation-detector dismissal-detector my-detector",
        "timeout": 30,
        "statusMessage": "Evaluating response quality..."
      }
    ]
  }
]
```

### For PostToolUse rules

Add a new matcher group or extend an existing one:

```json
"PostToolUse": [
  {
    "matcher": "tool_name_regex",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/runner.py my-detector",
        "timeout": 10,
        "statusMessage": "Checking output..."
      }
    ]
  }
]
```

### For other events

Create a new event entry following the same pattern.

## Rule Resolution Layers

Rules are loaded from multiple directories (highest priority wins by name):

```
1. <project>/.vaudeville/rules/    (project-specific overrides)
2. ~/.vaudeville/rules/             (user-global rules)
3. <plugin_root>/rules/             (bundled defaults)
```

Users can override bundled rules by placing a file with the same name in
their project or global directory.

## Workflow — Step by Step

### 1. Define the classification task

Ask: "What behavior am I trying to detect?" Write it as a one-sentence task.

### 2. Write the rule YAML

Create `rules/<name>.yaml` following the format above. Start with 4 examples
in the prompt (2 violation, 2 clean).

### 3. Write test cases

Create `tests/<name>.yaml` with at least 10 labeled examples.

### 4. Run the eval

```bash
uv run python -m vaudeville.eval --rule <name>
```

This scores the rule against its test cases. Target: **>90% accuracy**.

If accuracy is low:
- Add more examples to the prompt
- Make criteria more specific
- Check for ambiguous test cases
- Ensure labels are consistent between prompt and test file

### 5. Register in hooks.json

Add the rule name to the appropriate runner command in `hooks/hooks.json`.

### 6. Test end-to-end

Verify the full chain works:
1. Daemon is running (`/vaudeville:status`)
2. Hook fires on the correct event
3. Rule loads and classifies correctly
4. Block/warn/log action triggers as expected

## Existing Rules (Reference)

Read these for style and structure guidance:

| Rule | Event | Detects |
|------|-------|---------|
| `violation-detector` | Stop | Hedging, deferrals, unresolved findings, told-user-to-verify |
| `dismissal-detector` | Stop | Dismissing test/CI failures without evidence |
| `deferral-detector` | PostToolUse | "Follow-up PR" / "separate commit" language in PR replies |

## Expected Output

For each rule created, deliver:
1. The rule YAML file in `rules/`
2. The test cases file in `tests/`
3. Updated `hooks/hooks.json` registration
4. Eval results showing >90% accuracy

## What This Skill Doesn't Do

- Create JS/bash hooks (use hook-creator)
- Query session analytics (use session-analytics)
- Modify the daemon or runner infrastructure
- Override existing rules without explicit user approval
