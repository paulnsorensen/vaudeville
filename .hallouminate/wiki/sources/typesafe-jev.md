---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# TypeSafe (Jev)

Pydantic's TypeSafe (Jev) model documentation explains why the TypeSafe Jev model answers typed questions instead of generating text.
Pydantic publishes this official documentation.
This source is last verified on 2026-10-12.
Canonical source: [TypeSafe (Jev)](https://pydantic.dev/docs/ai/models/typesafe/)

## Typed questions instead of text generation

The TypeSafe Jev model answers typed questions about supplied text.
The TypeSafe Jev model does not generate free-form text; it returns bounded decisions.
The current Pydantic documentation describes TypeSafeModel as a decision-model integration.
Output and tool schemas define the questions the integration asks.

## Project relevance

TypeSafe (Jev) supports the distinction between a semantic answer and a workflow transition.
A model can judge supplied input or supplied history.
This source does not approve a persistent hook-state engine for Vaudeville.
See [Jev decision capabilities](../rules/jev-decision-capabilities.md).

## Version limits

TypeSafe (Jev) documentation is unversioned.
The inspected Vaudeville environment uses pydantic-ai-slim 2.47.0.
Its TypeSafeModel supports typed questions directly, without the newer separate DecisionModel module.
Do not assume every current documentation API exists in the installed version.
No live Jev API call runs during this research.

_Source: https://pydantic.dev/docs/ai/models/typesafe/ · Updated: 2026-10-12 · Supersedes: none_
