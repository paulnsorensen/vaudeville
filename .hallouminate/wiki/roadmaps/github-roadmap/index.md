---
created: 2026-10-12
---
# GitHub roadmap

Vaudeville's GitHub roadmap has one public-release milestone and two open release-readiness issues as of 2026-10-12.[^1]
The goals preserve GitHub intent and acceptance criteria.
This import is a dated snapshot, not an automatic synchronization.

## Public-release milestone

GitHub milestone [v0.2.0](https://github.com/paulnsorensen/vaudeville/milestone/1) aims to promote draft rules, list the plugin in the marketplace, and polish it.[^2]
The milestone remains open.
Its recorded due date is 2026-05-30, before this import date.
An all-state milestone query returns no assigned items.
Neither imported issue belongs to the milestone.[^1]
No implementation order or milestone dependency is inferred.
The milestone supplies intent but no detailed acceptance criteria.
It remains informational here rather than becoming an executable goal with invented criteria.

## Open release-readiness goals

GitHub's release-readiness issues define two imported goals outside the empty milestone.[^1]

| Goal | Source | GitHub labels | Import review |
| --- | --- | --- | --- |
| [Remove uv from the hook hot path](./hook-startup-latency.md) | [Issue #76](https://github.com/paulnsorensen/vaudeville/issues/76) | high, dx, release-readiness | Recheck the old rule-loading assumptions. |
| [Add Linux ARM CI and setup smoke coverage](./linux-arm-ci-smoke.md) | [Issue #81](https://github.com/paulnsorensen/vaudeville/issues/81) | medium, portability, release-readiness | Recheck obsolete GGUF and model-download requirements. |

Empty `prereqs` lists preserve the absence of documented dependencies.
They do not establish that concurrent implementation is safe.

## Implementation context

The GitHub planning snapshot lists six open pull requests, including four old drafts.[^1]
Open pull requests are implementation context, not additional roadmap commitments.
Stop-hook fixes #115 and hook-suggester work #114 merge on 2026-10-12.
They are not pending goals.[^3]
The [source snapshot](../../sources/github-planning-snapshot.md) records current pull requests and known stale requirements.

## Import limits

GitHub Projects cannot be inspected because the current token lacks `read:project`.
Project-board fields, item order, and private planning remain unknown.
GitHub remains authoritative for later state changes.
This import changes no GitHub items and installs no automatic synchronization.
Review stale issue criteria with the owner before implementation.

<!-- HALLOUMINATE:INDEX-START -->
- [hook-startup-latency](./hook-startup-latency.md) — Remove uv from the hook hot path
- [linux-arm-ci-smoke](./linux-arm-ci-smoke.md) — Add Linux ARM CI and setup smoke coverage
<!-- HALLOUMINATE:INDEX-END -->

[^1]: [GitHub planning snapshot](../../sources/github-planning-snapshot.md), fetched 2026-10-12; GitHub REST milestones, issues, and pull requests.
[^2]: https://github.com/paulnsorensen/vaudeville/milestone/1
[^3]: https://github.com/paulnsorensen/vaudeville/pull/115 and https://github.com/paulnsorensen/vaudeville/pull/114

_Source: GitHub REST and gh, 2026-10-12 · Updated: 2026-10-12 · Supersedes: none_
