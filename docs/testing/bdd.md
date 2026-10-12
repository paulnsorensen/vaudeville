# Behavior tests

The behavior suite runs five collected scenarios through the real `vaudeville` CLI and hook runner.
It uses pytest-bdd without replacing pytest or existing unit tests.
[pytest-bdd supports normal pytest collection and fixtures](https://pytest-bdd.readthedocs.io/en/stable/).

## Behavior trace

| Requirement | Scenario | Public boundary | Assertion | Representative failure probe |
| --- | --- | --- | --- | --- |
| Promotion persists and respects user ownership. | Promote the user rule without changing a same-named project rule. | `promote`, then separate `show --json` and `list --json` invocations. | Tier is `warn`, source is the user directory, and project bytes remain unchanged. | Keep the current tier instead of advancing it. Observed red. |
| Enable restores the prior tier. | Restore the prior tier after disabling a rule. | `disable`, `show --json`, `enable`, and later reads. | Tier changes from `warn` to `disabled` and back to `warn`. | Restore the default instead of the saved tier. Mutation unrun. |
| Non-interactive deletion requires confirmation. | Reject deletion without confirmation and preserve all rules. | `delete`, followed by separate public reads. | Exit status is 1, the confirmation diagnostic matches, and before/after rule state matches. Project bytes remain unchanged. | Delete before checking confirmation. Mutation unrun; the confirmation guard remains unchanged. |
| A blocked hook persists a violation. | A blocked tool call persists a violation visible in statistics. | `hooks/runner.py`, live daemon, Anthropic SDK, local HTTP provider, then `stats --json`. | Exact harness denial, event identity/action/tier/model, and stable statistics counts. | Increment the statistics total. Observed red at the exact count assertion. |
| Rule edits affect a live daemon. | Tier changes take effect without restarting the daemon. | Hook, `promote`, hook, `disable`, hook; one daemon process. | Warn, then deny, then allow; two provider calls and events; PID unchanged. | Restart or stale rule cache. Mutation unrun. |

Requirements come from rule administration, hook tier enforcement, and event statistics.
Bindings live in `tests/bdd/test_rule_admin.py` and `tests/bdd/test_live_hook.py`.
Features live in `tests/bdd/rule_admin.feature` and `tests/bdd/live_hook.feature`.

## Real components and isolation

Each scenario uses a temporary home, a real Git project, and real YAML rules.
The rule-administration scenarios run each command in a new process from a nested directory.
Setup writes rules because the CLI has no rule-creation command.
Public reads inspect persistence and project-file bytes after CLI changes.

The live-hook scenarios start one real daemon subprocess with an isolated socket and event log.
They invoke the real hook runner, pydantic-ai, Anthropic SDK, and CLI in subprocesses.
Only the external provider is substituted with a loopback HTTP server.
The server checks the request path, dummy key, model, prompt, exact user text (`<<<HOOK-DATA\nsecret\nHOOK-DATA>>>`), and output-tool schema.
It validates its tool response against the installed Anthropic message type.
Fixture errors fail the scenario instead of hiding behind fail-open behavior.
Live-hook child processes receive one fake provider key and no inherited credentials or proxies.

The resolver emits a warning when a project rule has the same name as a user rule.
The deletion assertion checks the final diagnostic line, not incidental resolver warnings.

## Local commands

```text
uv sync --locked --group dev
uv run --locked pytest --collect-only -q tests/bdd
uv run --locked pytest -q tests/bdd
just build
uv run --locked python scripts/smoke_wheel.py
```

The canonical local gate remains `just build`.
It runs formatting, lint, strict mypy, vulture, full pytest coverage, and diff-cover.
The executable gate currently requires 90% overall coverage and 95% changed-line coverage.
Its comments and `CLAUDE.md` describe lower thresholds; this change preserves the executable policy.

CI retains lint, format, mypy, vulture, and the full pytest suite.
Both test jobs use locked synchronization and run the installed-wheel smoke check.
The CI operating systems remain Ubuntu and macOS.
Existing CI coverage and type-check scope differ from `just build`; this change does not alter those policies.

### Harness temporary directories

This sandbox exposes its default temporary directory as a Git root to Python path checks.
Existing hook tests expect temporary directories without a Git ancestor.
The resolver finds the sandbox Git root instead of the test rules, so those tests fail.
Changing `TMPDIR` to `/tmp` does not resolve this sandbox behavior.
Use an approved, non-Git temporary location outside that mapping for validation.
This session uses `TMPDIR=/var/tmp` with approved execution outside the sandbox.
It also sets `UV_CACHE_DIR` to a writable cache directory.
Set `COLUMNS=120` for this validation run.
At 80 columns, an existing path-output test fails when Rich wraps the filename.
Do not change the resolver or weaken assertions to hide these environment issues.

## Wheel smoke check

`scripts/smoke_wheel.py` builds one wheel in a fresh temporary directory.
It exports pinned, hashed dependencies from `uv.lock`, excluding the editable project.
It creates a fresh environment and installs those dependencies plus the wheel.
It copies the BDD suite and the production hook runner outside the source tree.
An explicit plugin-root variable points the smoke suite to the copied runner.
It runs all five scenarios against the installed wheel with isolated Python startup.
Explicit plugin loading excludes unrelated pytest plugins.
The temporary directory is removed after success or failure.

## Regression evidence

The promotion probe temporarily changes `_ACTIVE_TIERS[idx + 1]` to `_ACTIVE_TIERS[idx]`.
The focused promotion scenario fails at the persisted-tier assertion: `log` differs from `warn`.
The probe exits with status 1.
The live-hook statistics probe changes `len(valid)` to `len(valid) + 1` temporarily.
The focused scenario fails because the reported total is 2 instead of 1.
After restoration, the focused scenario passes and the production diff is empty.

The other proposed mutations remain unrun.
The rejected-deletion scenario exercises the real failure path without changing its guard.

## Observed validation

The base revision is `4d5747a`.
Validation uses Python 3.13.13 and the updated locked dependencies.

- Collection finds exactly five scenarios.
- The restored BDD slice passes all five scenarios.
- The installed-wheel check passes all five scenarios outside the source tree.
- Strict mypy passes the new BDD bindings and wheel-check script.
- Ruff passes on the new BDD bindings and wheel-check script.
- The full gate passes 1,126 tests and skips one live-provider test.
- Overall line coverage is 98.99%.
- Diff-cover reports no applicable production lines in this test-only change.

The successful full-gate command is:

```text
UV_CACHE_DIR=/home/paul/.cache/codex-tmp/uv-cache TMPDIR=/var/tmp COLUMNS=120 just build
```

The full pytest run takes 65.65 seconds in that configuration.
This measurement is not a speed comparison.
Hosted CI, macOS execution, and the live typesafe provider test remain unrun.
The three other mutation probes in the trace remain unrun.
