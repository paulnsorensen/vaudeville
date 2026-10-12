# Vaudeville wiki

This wiki records Vaudeville's approved planning and source evidence.
Start with the [GitHub roadmap](./roadmaps/github-roadmap/index.md).
Read the [source snapshot](./sources/github-planning-snapshot.md) for dated GitHub state and coverage limits.
Read [supported rules](./rules/supported-rules.md) for the current rule catalog.
Read [Jev decision capabilities](./rules/jev-decision-capabilities.md) for model and schema limits.
Read [stateful rule patterns](./ideas/stateful-rule-patterns.md) for proposed, unapproved cross-event extensions.
Follow [wiki conventions](./wiki-conventions.md) when updating pages.

The initial wiki registration uses the main repository root.
The tracked configuration resolves the wiki inside each checkout.
Repository-local configuration uses `[[repository]]`, `name = "vaudeville"`, and `path = "."`.
GitHub Projects remain outside the import because the token lacks `read:project`.
No automatic synchronization is installed.

## Validation

Initial bootstrap validation on 2026-10-12 confirms nine indexed pages, valid local links, and source-exact acceptance criteria.
All ten retrieval checks meet their expected ranks.
`TMPDIR=/tmp just build` passes outside the sandbox at repository revision `127ee2f`.
It reports 1126 passed, 1 skipped, and 98.99% total coverage.
The sandboxed build reports 91 failures because temporary-root `.git` placeholders redirect project-root detection.
The focused external rerun passes all 39 hook-pipeline tests.
Project-root detection checks for the existence of `.git`, not whether Git considers the directory a repository.[^1]
Keep this environment difference in mind when interpreting test failures.
No application code or protected `.git` paths change during this setup.

[^1]: vaudeville/rules/cache.py:24-38; sandbox and external test runs at revision 127ee2f on 2026-10-12.

_Source: Approved repository registration, GitHub import, and validation runs · Updated: 2026-10-12 · Supersedes: none_

<!-- HALLOUMINATE:INDEX-START -->
- [ideas/](./ideas/index.md) — ideas
- [roadmaps/](./roadmaps/index.md) — roadmaps
- [rules/](./rules/index.md) — rules
- [sources/](./sources/index.md) — sources
- [log](./log.md) — Ingest log
- [wiki-conventions](./wiki-conventions.md) — Wiki conventions
<!-- HALLOUMINATE:INDEX-END -->
