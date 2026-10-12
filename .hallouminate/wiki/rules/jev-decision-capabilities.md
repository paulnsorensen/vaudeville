---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Jev decision capabilities

Jev answers typed semantic questions rather than generating free text.
Its upstream and installed adapter capabilities are broader than Vaudeville's current string-outcome rule schema.
This page separates model capability from supported application configuration.

## Typed question forms

Jev decision capabilities include the following forms in inspected TypeSafeModel 2.47.0.

| Primitive | Semantic question | Application exposure today |
| --- | --- | --- |
| Boolean | Does this predicate apply? | Encode yes/no as string outcome labels. |
| Pick-one choice | Which declared category applies? | Supported string outcomes. |
| Optional pick-one | Does none of the offered categories apply? | Use an explicit string label; nullable outcomes are not exposed. |
| Multi-label | Which options independently apply? | Not exposed by the current YAML output schema. |
| Bounded yes-probability | How strongly does the predicate apply? | Not an output field; current confidence comes from metadata. |
| Ordered rubric | Which described severity level applies? | Current string labels have no ordinal behavior. |
| Nested questions | Which separate properties apply? | Not exposed beyond outcome and optional reason. |

Pydantic's [Decision models](../sources/decision-models.md) documents these bounded question mappings.
A probability field is not arbitrary number generation.
An ordered rubric requires described levels.
[TypeSafe (Jev)](../sources/typesafe-jev.md) explains the model's typed decision role.

## Installed version and current application

Jev decision capabilities are version-sensitive.
The inspected environment has pydantic-ai-slim and pydantic-evals 2.47.0.
Its TypeSafeModel source confirms the listed primitive forms.
It has no separate models/decision.py module.
Current upstream documentation includes newer names and API forms.
This page does not claim those forms work in the installed version.
Vaudeville builds a string-Literal outcome and optional reason field.[^1]
Jev rules cannot declare reason: text.
Use an LLM for corrective rewrite text.[^2]
See [supported rules](./supported-rules.md).

## Independent judgements

Jev decision capabilities do not make simultaneous field answers depend on each other.
The Decision models source states that fields in one request are independent.
A separate reason question is not guaranteed to be conditioned on another answer.
Dependent judgements need separate steps.
A model answer is not proof that an observed test succeeds.

## Computation and generation limits

Jev decision capabilities differ from exact computation.
TypeSafe AI's [Jev 1.13 jaggedness](../sources/jev-1-13-jaggedness.md) warns about counting, date comparisons, and generation.
Counts, elapsed time, and verification exit codes should not become guesses about prose.
The source also warns about literal instructions, conflicting criteria, and option-order effects.
These are source constraints, not measured runtime accuracy in this research.

## Stateful proposal boundary

Jev decision capabilities can classify supplied event content or supplied history.
Persistent obligations, resets, event ordering, and deadlines remain application design questions.
The [stateful rule patterns](../ideas/stateful-rule-patterns.md) page records proposed extensions.
No new output schema or cross-event engine is approved.
No live Jev API call runs during this research.

[^1]: vaudeville/server/agents/output_types.py:20-43; pyproject.toml:27-35; TypeSafeModel 2.47.0 class documentation and question mapping inspected on 2026-10-12.
[^2]: vaudeville/rules/models.py:92-99; vaudeville/server/agents/rewrite.py:21-23,39-54.

_Source: Repository revision 127ee2f, installed TypeSafeModel 2.47.0, and linked primary sources · Updated: 2026-10-12 · Supersedes: none_
