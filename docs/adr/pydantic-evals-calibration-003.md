# ADR pydantic-evals-calibration-003: One `unsure:` block with an optional outcome filter

- Status: accepted
- Date: 2026-09-25
- Context: FU-1b spec `pydantic-evals-calibration` (fork F-D).

## Decision

`DecideRule` gets an optional `unsure: {below, action, outcomes?}` block.
When confidence is below `below` and the outcome is in `outcomes` (or `outcomes` is absent), `unsure.action` replaces the `on:` action.
The tier ceiling and precedence apply to it as to an `on:` action.
A rule with `unsure:` must name an explicit `model: typesafe:*`; the loader rejects it otherwise.
A None confidence keeps the `on:` action and logs `confidence-missing`.
The gate never applies inside an escalate hop, so escalation stays one hop.

## Alternatives

- A per-outcome `min_confidence` and `else:` action inside `on:`. It is the most flexible, but nests fallback actions in `Action`.
- A reserved `unsure` pseudo-outcome in `on:` plus `confidence_floor`. The name can collide with a real outcome, and the gate cannot tell outcomes apart.
- One global `min_confidence`. It escalates uncertain clean verdicts too.

## Rationale

For a hook, an uncertain block verdict is worth escalating, and an uncertain clean verdict usually is not.
Escalating clean verdicts adds latency to the common path.
The optional `outcomes` filter expresses that asymmetry in one readable block, and it reuses the existing `Action` type.
