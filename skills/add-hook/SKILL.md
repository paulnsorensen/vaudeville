---
name: add-hook
description: >
  Create a structural hook or a semantic vaudeville rule from a behavior
  description. Use when the user asks to add a hook, rule, detector, or
  enforcement for Claude Code behavior.
model: sonnet
context: fork
allowed-tools: Agent, Read, Glob, Grep, Bash, Write, Edit
---

# add-hook

Create one hook or rule that can change behavior at the selected event.

## Choose the mechanism

- Use a structural hook for file paths, commands, JSON fields, or exact patterns.
  Route this work to `vaudeville:hard-hook-writer`.
- Write a semantic rule directly for intent, tone, or other natural-language
  classification. Do not dispatch a separate SLM rule writer.
- Ask the user to choose when both mechanisms fit.

A structural hook is fast and deterministic. A semantic rule can recognize
variations, but it needs model inference and measured test cases.

## Check impact

1. Select the earliest event that contains the needed input.
2. Check whether the action has already happened at that event.
3. Use `PreToolUse` to prevent an action when possible.
4. Use `Stop` with `tier: block` only when the next turn can correct the result.
5. Reject a post-hoc rule when no tier can produce a useful correction.

`shadow` collects evidence. `warn` sends a nudge. `block` prevents a
tool call or forces a corrective continuation.

## Write a semantic rule

1. Read `examples/README.md` and relevant rules in `examples/rules/`.
2. Choose a rule name, event, outcomes, `on:` actions, and initial tier.
3. Write one typed rule YAML in the requested project or global rules directory.
4. Include balanced `test_cases` with `text` and `outcome`.
5. Run `vaudeville validate <name>`.
6. Run `uv run python -m vaudeville.eval --rule <name>`.
7. Report the rule path, metrics, tier, and any failed cases.

Use `tier: shadow` for an unmeasured rule. Map a violation to its intended
final action in `on:`; the tier limits the live action during tuning.
Do not use a separate test file or a `{text}` placeholder.

To tune the rule, revise one prompt or test-case boundary at a time and rerun
the evaluation. Use the host's `/loop` command for repeated agent turns when
available.

## Write a structural hook

Tell the user why an exact check fits. Then dispatch
`vaudeville:hard-hook-writer` with the behavior and target scope.

## Report

State what the hook catches, the event, and the effective tier. Give one
command that verifies the behavior. If the daemon is not running, ask the
user to check `/vaudeville:status` before relying on the rule.