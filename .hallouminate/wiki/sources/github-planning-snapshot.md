---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---

# GitHub planning snapshot

This page records the repository's GitHub planning state on 2026-10-12. It is a dated snapshot, not a live plan. GitHub remains authoritative for later changes.

## Repository summary

The repository is paulnsorensen/vaudeville.[^1]
The authenticated GitHub API returned one milestone, two open issues, and six open pull requests.
The milestone's explicit all-state item query returned no items.[^2]

This page does not treat pull requests as goals. It does not infer milestone membership or dependency relationships.

## Milestone

Milestone #1, v0.2.0, is open with a due date of 2026-05-30.
Its description is “First public milestone — promote draft rules to stable, marketplace listing, and polish.”
It was updated on 2026-04-23.
GitHub reports zero open issues and zero closed issues for it.
The explicit all-state item query returns no items.[^2]

The due date was past on the snapshot date. This does not confirm a release date. No issue or pull request is assigned to this milestone.

## Open issues

Both open issues have no milestone assignment.[^3]

| Issue | Priority / area | Snapshot intent |
|---|---|---|
| [#76: Drop uv run from hook hot path](https://github.com/paulnsorensen/vaudeville/issues/76) | high / dx; label: release-readiness | Remove uv cold-start overhead by using direct Python per-event hooks. |
| [#81: Add Ubuntu ARM CI and setup smoke job](https://github.com/paulnsorensen/vaudeville/issues/81) | medium / portability | Add Linux ARM CI and session-start/runner smoke checks. |

Issue #81 refers to gguf, model downloads, and setup.py. Those assertions are stale after merged PR #98. The inspected changelog records the superseding work.[^4] The current CI workflow still lists only ubuntu-latest and macos-15 runners. It includes an installed-wheel smoke check, but that does not prove full acceptance of #81.[^5]

Issue #76's current hook configuration calls runner.sh, which still invokes uv run. No fixed latency measurement was made.[^6] These current-state checks preserve context; they do not change issue acceptance criteria or imply completion.

## Open pull requests

These six pull requests were open on the snapshot date. Titles and draft state are provenance, not an assessment of readiness.[^7]

| Pull request | State | Title |
|---|---|---|
| [#116](https://github.com/paulnsorensen/vaudeville/pull/116) | non-draft | fix(hook): skip Claude Code Stop rules while background subagents run |
| [#101](https://github.com/paulnsorensen/vaudeville/pull/101) | non-draft | chore(deps): Bump the uv group across 1 directory with 2 updates |
| [#89](https://github.com/paulnsorensen/vaudeville/pull/89) | draft | CI: add ubuntu-24.04-arm matrix + end-to-end smoke job |
| [#86](https://github.com/paulnsorensen/vaudeville/pull/86) | draft | feat: Linux aarch64 gguf pre-flight — detect missing cmake/cc before uv sync stalls |
| [#84](https://github.com/paulnsorensen/vaudeville/pull/84) | draft | Drop uv run from hook hot path: use python3 directly for 150–400ms faster hook fires |
| [#83](https://github.com/paulnsorensen/vaudeville/pull/83) | draft | Harden daemon runtime dir ownership check + fail-open on non-POSIX hosts |

The snapshot records PRs #116 and #101 updated on 2026-10-12 and 2026-10-01. The four draft PRs were updated on 2026-04-23.[^7]

## Recently merged work

PR #115 delivers Stop-block limits when stop_hook_active is true.
PR #114 adds semantic Stop-rule candidates from final messages.
PR #119 adds CLI/live hook integration behavior tests.
GitHub records all three as merged on 2026-10-12.[^8]
They are no longer pending work in this snapshot.

## Coverage and limits

The queries covered all milestones, current open issues, current open pull requests up to 100 results, and explicit milestone items. The returned issue and pull-request counts were below the result limit.[^2][^3][^7]

The authenticated token lacked read:project permission, so the GitHub Projects query failed. This snapshot does not claim that Projects are empty and does not request an authentication change.[^9] No automatic sync is installed. Use GitHub for current status.

For the imported roadmap, see [GitHub roadmap](../roadmaps/github-roadmap/index.md).

## Provenance

The snapshot used GitHub REST APIs and gh on 2026-10-12. Local stale-note checks used repository revision 127ee2f. The page preserves fetched planning data and limited local checks; it does not make readiness judgments.

[^1]: https://github.com/paulnsorensen/vaudeville
[^2]: https://github.com/paulnsorensen/vaudeville/milestone/1
[^3]: https://github.com/paulnsorensen/vaudeville/issues/76 ; https://github.com/paulnsorensen/vaudeville/issues/81
[^4]: https://github.com/paulnsorensen/vaudeville/issues/81 ; https://github.com/paulnsorensen/vaudeville/pull/98 ; CHANGELOG.md:15-22
[^5]: .github/workflows/ci.yml:46,67-68
[^6]: hooks/hooks.json:20 ; hooks/runner.sh:19
[^7]: https://github.com/paulnsorensen/vaudeville/pull/116 ; https://github.com/paulnsorensen/vaudeville/pull/101 ; https://github.com/paulnsorensen/vaudeville/pull/89 ; https://github.com/paulnsorensen/vaudeville/pull/86 ; https://github.com/paulnsorensen/vaudeville/pull/84 ; https://github.com/paulnsorensen/vaudeville/pull/83
[^8]: https://github.com/paulnsorensen/vaudeville/pull/115 ; https://github.com/paulnsorensen/vaudeville/pull/114 ; https://github.com/paulnsorensen/vaudeville/pull/119
[^9]: GitHub Projects query via gh on 2026-10-12

_Source: GitHub REST and gh; repository revision 127ee2f · Updated: 2026-10-12 · Supersedes: none_
