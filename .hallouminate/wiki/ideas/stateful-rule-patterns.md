---
status: draft
owner: paulnsorensen
last_verified: 2026-10-12
confidence: low
---
# Stateful rule patterns

Stateful rule patterns are proposed extensions for cross-event obligations and temporal checks.
They are not supported Vaudeville configuration, an approved implementation spec, or committed roadmap goals.
Jev can supply semantic classifications; a separate application layer would own event state.

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

Stateful rule patterns are absent from the checked generic rule schema and request pipeline.
Current rule matching and target resolution use one event.[^1]
Confidence substitution and escalation are existing per-event behaviors.
Merged background-agent and repeated-Stop guards are special cases, not a declarative arming system.[^2]
See [supported rules](../rules/supported-rules.md).

## Unresolved design questions

Stateful rule patterns require an owner-approved contract before implementation.
Should obligations belong to a session, task, file, or changed revision?
What observed evidence qualifies to clear an obligation?
Must verification follow the latest relevant edit?
What happens after restart, cancellation, duplicate events, or late events?
Does the first extension include only arming, or also sequences, windows, and counters?
No persistence format, engine, thresholds, or lifecycle policy is selected.

## Confidence and next step

Stateful rule patterns have established conceptual precedent in the linked sources.
Their application to agent hooks remains speculative.
Use mold to define and validate the contract before implementation.
No live Jev API call or stateful prototype runs during this research.

[^1]: vaudeville/rules/models.py:67-85,172-185; vaudeville/server/hook/pipeline.py:142-152,530-560.
[^2]: https://github.com/paulnsorensen/vaudeville/pull/115; https://github.com/paulnsorensen/vaudeville/pull/116.

_Source: Cited primary sources and rule-pattern research inspected 2026-10-12 · Updated: 2026-10-12 · Supersedes: none_
