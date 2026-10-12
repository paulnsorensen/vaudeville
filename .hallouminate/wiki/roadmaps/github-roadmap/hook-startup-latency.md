---
kind: goal
slug: hook-startup-latency
roadmap: github-roadmap
created: 2026-10-12
prereqs: []
---
# Remove uv from the hook hot path

## Intent

GitHub [issue #76](https://github.com/paulnsorensen/vaudeville/issues/76) requests direct Python hook execution to remove repeated uv startup overhead.[^1]
The issue describes a 150–400 ms startup cost.
That value is a historical source claim, not a measurement made during this import.
The issue remains open and has no GitHub milestone.
Its labels are high, dx, and release-readiness.
Related [pull request #84](https://github.com/paulnsorensen/vaudeville/pull/84) remains an open draft.[^2]

## Import review

Issue #76 predates the typed rule core.
Its proposed changes mention removed rule-loading modules.
Current hook commands use `runner.sh`, which still invokes `uv run`.[^3]
No latency benchmark runs during this import.
Do not treat these source criteria as an approved implementation plan.
Revalidate the import against current code with the owner before execution.
The [GitHub planning snapshot](../../sources/github-planning-snapshot.md) preserves the planning context.

## Acceptance

Issue #76 provides the following acceptance criteria.
The text remains verbatim to avoid a silent contract change.[^1]

> - `just build` passes.
> - New test: `hooks/runner.py` imports cleanly with `PYTHONPATH` set to plugin root under a system Python that lacks PyYAML (assert via `subprocess` with a stripped `sys.path`).
> - `hooks.json` no longer contains `uv run` for the per-event hooks; only `session-start.sh` uses `uv run`.
> - Manual timing note in PR description: before/after `time python3 hooks/runner.py --event Stop </dev/null`.

[^1]: https://github.com/paulnsorensen/vaudeville/issues/76, fetched 2026-10-12.
[^2]: https://github.com/paulnsorensen/vaudeville/pull/84, open draft at import.
[^3]: hooks/hooks.json:15-23; hooks/runner.sh:18-19; typed-core migration: https://github.com/paulnsorensen/vaudeville/pull/98. Code inspected at revision 127ee2f.

_Source: GitHub issue #76 and repository revision 127ee2f · Updated: 2026-10-12 · Supersedes: none_
