---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Supported rules

Vaudeville supports two semantic rule types: decide and rewrite.
Confidence gates, judgement, and escalation are behaviors within those types, not additional schema types.[^1]
This page describes checkout revision 127ee2f, inspected on 2026-10-12.

## Semantic rule types

Supported rules use a typed YAML union.

| Behavior | Representation | Effect |
| --- | --- | --- |
| Binary classification | decide with two string outcomes | Select a declared label. |
| Multi-way classification | decide with several outcomes | Select one declared label. |
| Model judgement | decide with a configured model | Judge meaning; an LLM may give a free-text reason. |
| Jev classification | decide with a typesafe model | Select an outcome and optional predefined reason bucket. |
| Low-confidence substitution | decide.unsure | Substitute an action below the configured confidence threshold. |
| One-hop escalation | escalate action | Evaluate another decide rule on the same event. |
| Corrective rewriting | named rewrite target | Rewrite allowlisted string fields; use feedback when tool input is absent. |

The TypeSafe (Jev) source documents typed answers, not free-form generation.
See [TypeSafe (Jev)](../sources/typesafe-jev.md) and [Jev decision capabilities](./jev-decision-capabilities.md).
The current app constructs string-Literal outcomes and optional reasons.
It does not expose all upstream Jev field forms.[^2]

## Actions and precedence

Supported rules have ten action names.[^3]

| Action | Meaning |
| --- | --- |
| allow | Continue. |
| log | Record the decision. |
| warn | Send a supported warning. |
| block | Reject a supported tool event or prevent supported Stop completion. |
| feedback | Return corrective guidance. |
| rewrite | Invoke a named rewrite rule. |
| escalate | Invoke a named decide rule once. |
| ask | Request permission where supported. |
| add-context | Add configured context where supported. |
| run | Invoke a named command from trusted user configuration. |

The primary precedence is block, ask, rewrite, feedback, then warn.
Context, log, and command effects can accompany the winning primary action.[^4]
Harness event support limits each action.
Unsupported channels can downgrade an action.

## Tiers and matching

Supported rules have five rollout tiers: disabled, shadow, log, warn, and block.
Disabled rules skip evaluation.
Shadow and log tiers suppress every action except allow and log.
Warn downgrades block, ask, rewrite, and feedback.
Warn does not disable named commands or add-context.[^5]
Draft files skip loading.
A rule matches its declared event and optional exact tool names separated by a vertical bar.
Decide rules can carry inline test cases.[^6]

## Safety and collections

Supported rule collections load user-global and project YAML.
Project rules cannot replace names owned by the user-global layer.
Bundled examples remain inactive until copied to a rule layer.[^7]
Rewrite targets cannot change raw Bash commands or fields that route I/O or execute code.
Project rewrite rules require scoped, non-MCP tool matching.
Rule files cannot carry command argv.[^8]
Native structural hooks provide exact checks outside this semantic YAML schema.[^9]

## Bundled rules and documentation drift

Supported examples have these actual YAML values.

| Rule | Event | Actual tier | Intended violation action |
| --- | --- | --- | --- |
| git-gate | Stop | shadow | block |
| deferral-detector | PreToolUse | warn | block |

The examples README describes both rules as warn.
The git-gate YAML instead sets shadow.
The YAML and implementation control runtime behavior.[^10]
Older examples prose also describes different log-tier behavior and global-rule overrides.
This page follows the current implementation.

## Cross-event limit

Supported rule matching uses the current event.
Escalation and rewrite targets must match that same event.[^11]
General cross-event arming is not present in the checked schema and pipeline.
See [stateful rule patterns](../ideas/stateful-rule-patterns.md) for unapproved proposals.

[^1]: vaudeville/rules/models.py:67-85,172-214,234-244.
[^2]: vaudeville/server/agents/output_types.py:20-43; vaudeville/rules/models.py:92-99.
[^3]: vaudeville/rules/actions.py:13-43.
[^4]: vaudeville/server/hook/precedence.py:1-7,59-88.
[^5]: vaudeville/rules/policy.py:11-30; vaudeville/server/hook/tier.py:12-24.
[^6]: vaudeville/rules/loader.py:37-51; vaudeville/server/hook/pipeline.py:142-148,220-223.
[^7]: vaudeville/rules/loader.py:107-130,146-181.
[^8]: vaudeville/rules/models.py:193-230; vaudeville/rules/loader.py:172-180.
[^9]: skills/add-hook/SKILL.md:16-25.
[^10]: examples/rules/git-gate.yaml:70-73; examples/rules/deferral-detector.yaml:50-53; examples/README.md:3,41-47.
[^11]: vaudeville/server/hook/pipeline.py:142-152,530-560.

_Source: Repository revision 127ee2f and cited primary documentation · Updated: 2026-10-12 · Supersedes: conflicting examples prose on tiers and global-rule ownership_
