---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Jev 1.13 jaggedness

TypeSafe AI's Jev 1.13 jaggedness guidance identifies limits in counting, date arithmetic, and text generation.
TypeSafe AI publishes this official documentation.
This source is last verified on 2026-10-12.
Canonical source: [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

## Exact computation limits

Jev 1.13 jaggedness guidance states that Jev is not a calculator.
Counting characters, terms, or list entries is unreliable.
Date ordering and elapsed-time comparisons are also unreliable.
The source separates semantic extraction from deterministic computation.

## Semantic judgement limits

Jev 1.13 can read instructions more literally than the author intends.
Contradictory instructions and criteria reduce reliability.
The order of choice options can affect an answer.
The source recommends clear criteria and checks across option order.
Jev 1.13 is not trained to generate text.
Forcing generation through chained choices performs poorly and slowly.

## Project relevance

Jev 1.13 jaggedness supports keeping counters and deadlines outside semantic model judgements.
Verification exit codes and event ordering should not become guesses about prose.
See [Jev decision capabilities](../rules/jev-decision-capabilities.md).
The source applies to Jev 1.13 and reports review on 2026-10-02.
No live model behavior is measured here.

_Source: https://docs.typesafe.ai/model-jaggedness/jev-1.13 · Updated: 2026-10-12 · Supersedes: none_
