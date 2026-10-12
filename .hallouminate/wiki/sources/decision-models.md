---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Decision models

Pydantic's Decision models documentation describes the mappings from typed output fields to bounded semantic questions.
Pydantic publishes this official documentation.
This source is last verified on 2026-10-12.
Canonical source: [Decision models](https://pydantic.dev/docs/ai/models/decision/)

## Typed question mappings

Decision models map Boolean fields to yes/no questions.
String Literal and Enum fields select declared choices.
Bounded probability fields answer a yes/no probability question, not arbitrary arithmetic.
Described ordered levels support rubric scoring.
Lists and option dictionaries support independent yes/no decisions for each option.
Nested models group supported fields.
Optional pick-one fields can represent that no offered category applies.

## Question independence

Decision models answer fields in one request independently.
One field cannot use another field's answer from that same request.
Dependent judgements need separate steps.
A separately selected reason is not guaranteed to be conditioned on the selected outcome.

## Project relevance and version limits

Decision models document upstream capacity, not Vaudeville's rule schema.
Vaudeville currently constructs one string-Literal outcome and an optional reason field.
See [Jev decision capabilities](../rules/jev-decision-capabilities.md).
The current documentation is unversioned.
Installed TypeSafeModel 2.47.0 confirms several primitives, but not every newer API form.

_Source: https://pydantic.dev/docs/ai/models/decision/ · Updated: 2026-10-12 · Supersedes: none_
