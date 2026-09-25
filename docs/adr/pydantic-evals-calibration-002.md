# ADR pydantic-evals-calibration-002: Confidence comes from Jev provider_details and gates actions

- Status: accepted
- Date: 2026-09-25
- Context: FU-1b spec `pydantic-evals-calibration` (fork F-C).

## Decision

The decide output type has no `confidence` field for any rule.
For `typesafe:` models, `decide` reads the chosen outcome's probability from `result.response.provider_details['probabilities']['outcome']`, then `provider_details['confidence']['outcome']`, else None.
Other providers give None.
Calibration reports ROC AUC, KS, Brier, ECE, a threshold sweep, and a recommended `below` for the `unsure:` gate (ADR 003).

## Alternatives

- Report only, with no gating.
- Repeat-run agreement as a confidence proxy for every provider. It costs N model calls and measures stability, not calibration.
- Defer calibration until Jev access exists.
- Keep a self-reported confidence field for LLM rules, labelled as self-report.

## Rationale

PR 98 read confidence from a self-reported output field (`output_types.py:31`, `decide.py:64`).
An LLM's self-reported number is not calibrated.
For Jev, a plain float output field is a question that Jev answers, not a calibrated value (TypeSafe docs and pydantic-ai 2.47.0 `models/typesafe.py`).
Nothing consumed confidence before this change, so calibration had no purpose until gating gave it one.
Evidence: `.cheese/research/pydantic-evals-typesafe/pydantic-evals-typesafe.md`.
