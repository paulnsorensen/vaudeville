# ADR pydantic-evals-calibration-001: Eval cases stay inline; the Dataset is built in memory

- Status: accepted
- Date: 2026-09-25
- Context: FU-1b spec `pydantic-evals-calibration` (fork F-A).

## Decision

Rule YAML keeps its inline `test_cases:` list of `{text, outcome}`.
The eval builds a `pydantic_evals.Dataset` from those cases at run time and scores it with `Dataset.evaluate_sync`.
Report evaluators are set in code, the same for every rule.

## Alternatives

- A separate `<rule>.cases.yaml` in the native pydantic-evals format. It allows per-rule evaluators in the file. It breaks every existing user and project rule, splits a rule into two files, and moves the outcome check across files, the cache key, and the tuner rollback.
- Inline cases plus an optional separate dataset. This gives two case sources to merge.
- No change (keep the custom harness).

## Rationale

An in-memory `Dataset` is the same object that `Dataset.from_file` returns, so `evaluate_sync`, the report evaluators, and any later optimizer that takes a `Dataset` work the same.
pydantic-ai has no prompt optimizer today (docs checked 2026-09-25).
The rule contract does not change, and the loader keeps its check that every case outcome is in `outcomes`.
A native file export stays possible as follow-up FU-1b-3.
