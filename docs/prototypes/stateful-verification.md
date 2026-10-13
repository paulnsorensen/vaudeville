# Stateful verification prototype

Add this block to `~/.vaudeville/config` to opt in:

```yaml
prototype_verification:
  commands:
    - "pytest -q"
  tier: block
```

The default tier is `warn`. Use `block` only for a deliberate demo. Commands match the Bash `tool_input.command` string exactly. The prototype does not run them. Configure commands that verify the whole project from any project directory, such as a `just` recipe.

Run `uv run python scripts/prototype_verification.py` to replay synthetic JSON hook events. The script calls the real `handle_hook_request` pipeline. It writes no user configuration and calls no model.

A successful main-agent Claude Code `PostToolUse` Edit or Write inside the canonical project root creates an obligation. A matching foreground Bash command can start from any directory in the project. Its `PreToolUse` records the current edit generation, `tool_use_id`, and directory. A successful `PostToolUse` with the same identity, command, and directory clears only that generation. Structured Bash `stdout` and `stderr` strings plus `interrupted: false` establish observed completion. An explicit `exit_code` must be integer zero. The [Claude Code hook documentation](https://code.claude.com/docs/en/hooks#posttooluse) defines the PostToolUse event and Bash response shape; it does not guarantee a uniform exit code.

Stop names each configured command and reports an uncleared obligation through the existing tier, precedence, logging, and Stop-loop paths. SessionEnd removes the session's scopes. The tracker uses received order only, a lock around state changes, 256 recent scopes, and 512 recent attempts and completions per scope. Scope eviction and daemon restart lose obligations and fail open. Completion-history eviction loses deduplication and may re-arm replayed edits. A retained duplicate completion cannot replay.

This is not a generic rule type. It covers only Edit and Write, not other Bash, MCP, external, or subagent edits. The tracker does not inspect assistant prose, persist state, count windows, or infer verification coverage. A successful command only proves observed tool completion, not test quality.

## Pending-gated Stop rules

A decide rule with `when: verification-pending` runs only on a Stop that has a pending obligation. Use it to ask Jev a semantic question about the final message. The rule never clears an obligation; only an observed command does.

```yaml
type: decide
name: unverified-claim
event: Stop
when: verification-pending
model: typesafe:jev-1.13
prompt: >-
  Does the assistant's final message claim that tests, builds, or checks
  passed for its latest changes?
outcomes: [claims-verified, no-claim]
reasons:
  unverified: You claim verification, but no configured command passed after the latest edit.
"on":
  claims-verified: block
tier: block
```

With a `warn` gate, this rule blocks only a false verification claim. An honest Stop gets the gate warning. A Stop with no pending obligation skips the rule and makes no model call. `when` requires `event: Stop`. Without `prototype_verification`, nothing is pending, so the rule never runs. An `escalate` action that targets the rule bypasses `when`.