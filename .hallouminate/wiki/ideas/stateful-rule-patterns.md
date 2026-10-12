---
status: draft
owner: paulnsorensen
last_verified: 2026-10-12
confidence: low
---
# Stateful rule patterns

General stateful rule patterns remain proposed extensions, not a declarative YAML rule type or committed roadmap goals.
An opt-in verification-obligation prototype explores arming and evidence-qualified clearing; the permanent design remains unapproved.[^3]
Jev can supply semantic classifications; application code owns event state.

## Candidate pattern families

Stateful rule patterns can distinguish an event predicate from an obligation across several events.

| Proposed pattern | Illustrative hook behavior |
| --- | --- |
| Armed obligation | A relevant Write arms must-test; successful verification clears it; Stop checks pending state. |
| Ordered sequence | Require review, then edit, then verification. |
| Missing follow-up | Detect Stop without required verification after the relevant edit. |
| Time window | Require a qualifying follow-up within a declared interval. |
| Counter or budget | React after a declared number of observed failed attempts. |
| Composite gate | Combine semantic labels and exact event fields with AND/OR. |
| Evidence-qualified clearing | Clear only on observed successful verification for the relevant scope. |

Apache Flink's [Event Processing (CEP)](../sources/event-processing-cep.md) documents sequences, occurrence quantifiers, negative patterns, and windows.
These hook examples are inferred applications, not Flink-provided agent behavior.
AWS's [EventBridge comparison operators](../sources/eventbridge-pattern-operators.md) documents current-event Boolean, numeric, and presence predicates.
A missing event field does not prove absence from earlier event history.

## Illustrative arming lifecycle

Stateful rule patterns could use this unapproved lifecycle.
A meaningful edit creates a pending verification obligation.
An observed successful verification event for the relevant changes can clear it.
When the harness emits Stop, the rule evaluator checks the remaining obligation.
The evaluator gates an actual Stop event; it does not synthesize the harness event.
A test run before the latest relevant edit may not satisfy the later obligation.
This is design reasoning, not executable rule syntax.

## Semantic and deterministic evidence

Stateful rule patterns could use Jev to classify whether an edit is meaningful or which verification category applies.
Code would track pending obligations, event order, counts, and deadlines.
[Jev decision capabilities](../rules/jev-decision-capabilities.md) separates typed semantic answers from exact computation.
TypeSafe AI's [Jev 1.13 jaggedness](../sources/jev-1-13-jaggedness.md) warns against exact counting and date arithmetic.
Do not replace observed test results with a model's interpretation of completion prose.

## Current implementation boundary

General stateful rule patterns remain absent from the generic decide/rewrite YAML schema.
The prototype branch adds one specialized cross-event verification gate outside that schema.[^3]
Current rule matching and target resolution use one event.[^1]
Confidence substitution and escalation are existing per-event behaviors.
Merged background-agent and repeated-Stop guards are special cases, not a declarative arming system.[^2]
See [supported rules](../rules/supported-rules.md).

## Verification obligation prototype

The verification obligation prototype on `prototype/stateful-verification` uses temporary session/project scope and in-memory state.[^3]
A successful main-agent Claude Code Edit or Write inside the project arms a verification obligation.
A configured foreground Bash command must start from the project root.
Its start records the edit generation and tool-use identity.
Observed successful completion clears only that generation; verification started before another edit cannot clear the newer work.
Failed, unknown, mismatched, and retained duplicate evidence do not clear later obligations.
Stop checks pending obligations through existing tiers, action precedence, and continuation guards.
The prototype evaluates emitted Stop events; it does not create them.
The prototype limitations include unobserved Bash, MCP, external, and subagent edits.
Restart and bounded scope eviction lose obligations; bounded history eviction loses replay protection.
Configured commands must verify the whole project; successful tool completion does not establish test quality.
This prototype calls no model and adds no generic rule syntax, persistence, windows, or counters.[^3]

## Unresolved design questions

Stateful rule patterns require an owner-approved permanent contract before stabilization.
The prototype assumptions do not settle the wider design.
Should obligations belong to a session, task, file, or changed revision?
What observed evidence qualifies to clear an obligation?
Must verification follow the latest relevant edit?
What happens after restart, cancellation, duplicate events, or late events?
Does the first extension include only arming, or also sequences, windows, and counters?
No permanent persistence format, engine, thresholds, or lifecycle policy is selected.

## Confidence and next step

Stateful rule patterns have established conceptual precedent in the linked sources.
Their application to agent hooks remains speculative.
Use mold to define and validate the permanent contract before stabilization.
The original research runs no live Jev API call or stateful prototype.
The later prototype replays synthetic events; no live Claude Code session is verified.[^3]

[^1]: vaudeville/rules/models.py:67-85,172-185; vaudeville/server/hook/pipeline.py:142-152,530-560.
[^2]: https://github.com/paulnsorensen/vaudeville/pull/115; https://github.com/paulnsorensen/vaudeville/pull/116.
[^3]: Prototype branch based on 3da9451; vaudeville/server/user_config.py:63-87; vaudeville/server/hook/obligations.py:35-133; vaudeville/server/hook/pipeline.py:140-183; scripts/prototype_verification.py:13-61.

_Source: Cited primary research and inspected prototype branch based on 3da9451 · Updated: 2026-10-12 · Supersedes: unqualified absence of a stateful prototype_
