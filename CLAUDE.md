# Vaudeville

SLM-powered hook enforcement plugin for Claude Code. Uses Phi-4-mini (3.8B, int4) to classify AI responses against quality rules.

## Build & Test

```bash
just check        # lint + typecheck + test
just coverage     # tests with line coverage report (fails under 70%)
just test         # tests only
just lint         # ruff check + format check
just typecheck    # mypy strict
just fmt          # auto-format
just eval         # run eval harness against bundled rules
```

## Quality Gates

- `just check` must pass before committing
- `just coverage` for coverage verification

### Coverage Policy

- **New code must have 90%+ line coverage.** No exceptions. If you add a function, test it.
- **Boy Scout Rule**: when touching code adjacent to your change (same file or closely coupled module), add tests for uncovered lines you encounter. Leave coverage better than you found it.
- Overall project floor is 70% (enforced by `just coverage`). Ratchet this up as coverage improves. The 90% rule applies per-change, not retroactively to legacy code.
- Hardware-dependent modules (MLX/GGUF backends, setup.py, `__main__.py`) are excluded from coverage metrics.

## Architecture

Vertical slices under `vaudeville/`:
- `core/` — protocol, client, rules (stdlib-only, safe for hook scripts)
- `server/` — daemon, inference backends (MLX, GGUF)
- `eval.py` — eval harness for rule accuracy testing

Hook entry point: `hooks/runner.py` (thin, stdlib-only, fail-open)

## Key Patterns

- **Fail-open everywhere**: daemon down → allow, inference error → allow, unknown rule → allow
- **Event-aware truncation**: input text is condensed to fit within token budget, preserving structure boundaries (tool calls, assistant turns)
- **Prompt injection defense**: VERDICT:/REASON: markers are neutralized with zero-width spaces before interpolation
- **Deterministic inference**: both backends use temp=0.0
