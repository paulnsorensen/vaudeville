# Example Rules

Starter rules for vaudeville. These are **not active by default** — copy them to a rules directory to enable.

## Activating Rules

**Global** (all projects):
```bash
cp examples/rules/*.yaml ~/.vaudeville/rules/
```

**Per-project** (overrides global):
```bash
mkdir -p .vaudeville/rules/
cp examples/rules/*.yaml .vaudeville/rules/
```

## Included Rules

| Rule | Event | What it catches |
|------|-------|-----------------|
| `violation-detector` | Stop | Hedging, incomplete work, unresolved review findings |
| `dismissal-detector` | Stop | Dismissing test/CI failures without evidence |
| `deferral-detector` | PreToolUse | Deferring reviewer concerns to follow-up PRs |

## Rule Format

```yaml
name: my-rule              # unique identifier
event: Stop                # Claude Code hook event to trigger on
prompt: |                  # few-shot classification prompt
  Classify as "violation" or "clean".
  ...
  {text}                   # placeholder — replaced with hook input
context:
  - field: last_assistant_message   # JSON path into hook input
labels: [violation, clean]          # valid classification labels
action: block                       # block, warn, or log
message: "Reason: {reason}"         # verdict message template
threshold: 0.5                      # minimum confidence to trigger (0.0-1.0)
```

### Context sources

Rules extract text to classify from hook input via `context` entries:

- `field: <json.path>` — dot-notation path into the hook JSON (e.g., `last_assistant_message`, `tool_input.body`)
- `file: <path>` — read from disk (relative paths resolve from plugin root)

### Actions

- `block` — reject the response, force retry
- `warn` — show warning, allow to continue
- `log` — record silently

## Writing Your Own

1. Copy an example rule as a starting point
2. Edit the `prompt` with your classification criteria and few-shot examples
3. Set the `event` to the hook point you want (Stop, PostToolUse, UserPromptSubmit, etc.)
4. Set `context` to extract the right text from the hook input
5. Set `threshold` — start at 0.5, tune with `just eval --threshold-sweep`
6. Test with `just eval-rule <rule-name>` (requires test cases in `tests/<rule-name>.yaml`)
