---
kind: goal
slug: linux-arm-ci-smoke
roadmap: github-roadmap
created: 2026-10-12
prereqs: []
---
# Add Linux ARM CI and setup smoke coverage

## Intent

GitHub [issue #81](https://github.com/paulnsorensen/vaudeville/issues/81) requests Linux aarch64 CI and end-to-end setup smoke checks.[^1]
The issue aims to detect portability and hook-installation regressions.
It remains open and has no GitHub milestone.
Its labels are medium, portability, and release-readiness.
Related [pull request #89](https://github.com/paulnsorensen/vaudeville/pull/89) remains an open draft.[^2]

## Import review

Issue #81 still refers to GGUF builds, model downloads, and `vaudeville/setup.py`.
The typed-core migration removes the local inference backends and model-download step.[^3]
Current CI tests Ubuntu and macOS, without a Linux ARM matrix entry.
It also includes an installed-wheel smoke test.[^4]
That test does not establish completion of the issue's setup smoke criteria.
Do not execute the historical GGUF requirements without owner review.
Preserve them below until the owner approves a current acceptance contract.
The [GitHub planning snapshot](../../sources/github-planning-snapshot.md) records the source limits.

## Acceptance

Issue #81 provides the following acceptance criteria.
The text remains verbatim to avoid a silent contract change.[^1]

> - `just build` passes.
> - CI matrix shows a green `test (ubuntu-24.04-arm, gguf)` job on the PR.
> - CI shows a green `smoke` job that exercises `session-start.sh` and `runner.py` without a real model.
> - Intentionally breaking `session-start.sh` (e.g. wrong backend detection) causes the smoke job to fail.

[^1]: https://github.com/paulnsorensen/vaudeville/issues/81, fetched 2026-10-12.
[^2]: https://github.com/paulnsorensen/vaudeville/pull/89, open draft at import.
[^3]: CHANGELOG.md:15-22; https://github.com/paulnsorensen/vaudeville/pull/98.
[^4]: .github/workflows/ci.yml:42-68, inspected at revision 127ee2f.

_Source: GitHub issue #81 and repository revision 127ee2f · Updated: 2026-10-12 · Supersedes: none_
