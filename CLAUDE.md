# Vaudeville

pydantic-ai-powered hook enforcement plugin for Claude Code. A daemon evaluates YAML rules against hook events using configurable LLM providers.

## Build & Test

```bash
just build        # FULL validation: autoformat + lint(+autofix) + typecheck + tests + coverage + 90% diff-cover. ALL AGENTS RUN THIS.
just check        # lint + typecheck only (no autofix, no tests) — fast pre-commit smoke test
just coverage     # tests with line coverage report (fails under 70%)
just test         # tests only
just lint         # ruff check + format check
just typecheck    # mypy strict
just fmt          # auto-format
just eval         # run eval harness against bundled rules
```

## Quality Gates

- **`just build` is the canonical validation command.** All agents (cook, press, age sub-agents, fromage pipeline, ad-hoc edits) MUST run `just build` and confirm a clean exit before declaring work complete. Do not substitute `just check` or partial subsets — `build` is the single source of truth.
- `just build` autoformats, autofixes lint, runs the full pytest suite with coverage, and fails if new/changed lines vs `origin/main` fall below 90% line coverage.
- All heavy subcommands inside `just build` are wrapped with `rtk` so token consumption stays bounded when run from a Claude Code session.
- `just check` remains available as a fast pre-commit smoke test, but is NOT a substitute for `just build` before completion.

### Coverage Policy

- **New code must have 90%+ line coverage.** No exceptions. If you add a function, test it.
- **Boy Scout Rule**: when touching code adjacent to your change (same file or closely coupled module), add tests for uncovered lines you encounter. Leave coverage better than you found it.
- Overall project floor is 70% (enforced by `just coverage`). Ratchet this up as coverage improves. The 90% rule applies per-change, not retroactively to legacy code.
- `setup.py` and `__main__.py` are excluded from coverage metrics.

## Architecture

Vertical slices under `vaudeville/`:
- `core/` — protocol, client, rules (stdlib + pure-Python deps only, no native/platform-specific imports — safe for hook scripts)
- `server/agents/` — pydantic-ai decide and rewrite agents, model resolution
- `server/harness/` — harness-specific hook payload adapters (Claude Code and others)
- `server/effects/` — side effects the pipeline can trigger (rewrite, run-command)
- `server/hook/` — the hook pipeline: tier ceiling, precedence, `handle_hook_request`
- `eval.py` — eval harness for rule accuracy testing

Hook entry point: `hooks/runner.py` (thin, stdlib-only, fail-open). Config lives at `~/.vaudeville/config` (default model, providers, commands).

## Key Patterns

- **Fail-open everywhere**: daemon down → allow, model error → allow, unknown rule → allow
- **Data delimiting**: untrusted tool input is delimited before interpolation into prompts (`server/agents/delimit.py`) to resist prompt injection
- **Deterministic inference**: model calls use temp=0.0
