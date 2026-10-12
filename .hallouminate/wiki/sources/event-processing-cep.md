---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Event Processing (CEP)

Apache Flink's Event Processing (CEP) documentation describes event sequences, negative patterns, windows, and occurrence quantifiers.
Apache Flink publishes this official documentation.
This source is last verified on 2026-10-12.
Canonical source: [Event Processing (CEP)](https://nightlies.apache.org/flink/flink-docs-release-2.2/docs/libs/cep/)

## Sequence and occurrence patterns

Event Processing (CEP) describes sequences of events connected by conditions.
Strict sequences require adjacent matches.
Relaxed sequences permit intervening events.
Quantifiers can require one, several, or a bounded number of occurrences.
Conditions can inspect previously accepted events.

## Negative patterns and windows

Event Processing (CEP) supports negative patterns between sequence points.
A terminal negative pattern needs a time bound.
A within window bounds the time allowed for a sequence.
An incomplete sequence can expire.
Event-time order depends on watermark assumptions.
Late events need an explicit handling policy.

## Project relevance and limits

Event Processing (CEP) supplies precedent for [stateful rule patterns](../ideas/stateful-rule-patterns.md).
The Write, verification, and Stop examples are inferred applications, not Flink-provided agent hooks.
This page does not recommend adopting Flink.
The inspected page is Flink 2.2 documentation and labels that release documentation out of date.
No latest-version or implementation-compatibility claim is made.

_Source: https://nightlies.apache.org/flink/flink-docs-release-2.2/docs/libs/cep/ · Updated: 2026-10-12 · Supersedes: none_
