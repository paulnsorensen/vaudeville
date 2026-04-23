# Vaudeville

SLM-powered hook enforcement plugin for Claude Code. Uses Phi-4-mini (3.8B, int4) to classify AI responses against quality rules.

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
- Hardware-dependent modules (MLX/GGUF backends, setup.py, `__main__.py`) are excluded from coverage metrics.

## Architecture

Vertical slices under `vaudeville/`:
- `core/` — protocol, client, rules (stdlib + pure-Python deps only, no native/platform-specific imports — safe for hook scripts)
- `server/` — daemon, inference backends (MLX, GGUF)
- `eval.py` — eval harness for rule accuracy testing

Hook entry point: `hooks/runner.py` (thin, stdlib-only, fail-open)

## Key Patterns

- **Fail-open everywhere**: daemon down → allow, inference error → allow, unknown rule → allow
- **Event-aware truncation**: input text is condensed to fit within token budget, preserving structure boundaries (tool calls, assistant turns)
- **Prompt injection defense**: VERDICT:/REASON: markers are neutralized with zero-width spaces before interpolation
- **Deterministic inference**: both backends use temp=0.0
