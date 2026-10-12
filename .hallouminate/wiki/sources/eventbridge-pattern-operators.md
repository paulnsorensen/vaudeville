---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Comparison operators for use in event patterns in Amazon EventBridge

AWS's Comparison operators for use in event patterns in Amazon EventBridge documentation describes current-event Boolean, numeric, and field-presence predicates.
Amazon Web Services publishes this official documentation.
This source is last verified on 2026-10-12.
Canonical source: [Comparison operators for use in event patterns in Amazon EventBridge](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-pattern-operators.html)

## Current-event predicates

EventBridge comparison operators support conjunction and alternatives across event fields.
Numeric conditions can match exact values or ranges.
Anything-but conditions exclude specified values.
Exists conditions test presence or absence of a field.
These comparisons apply to one event payload.

## Field absence is not history absence

EventBridge exists matching checks a field in the JSON of the event.
It does not prove that an earlier verification event never occurs.
A cross-event obligation therefore differs from a missing-field predicate.

## Project relevance

EventBridge comparison operators supply precedent for composite event gates.
See [stateful rule patterns](../ideas/stateful-rule-patterns.md).
Using those concepts in Vaudeville remains a proposal.
This source does not select an engine, syntax, or persistence design.
No EventBridge deployment or API call is tested.

_Source: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-pattern-operators.html · Updated: 2026-10-12 · Supersedes: none_
