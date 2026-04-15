# Contributing to Vaudeville

Contributions are welcome — bugs, rules, docs, or features. Open an issue before starting significant work to avoid building something that doesn't fit.

## Development Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and [just](https://github.com/casey/just).

```bash
git clone https://github.com/paulnsorensen/vaudeville
cd vaudeville
just install    # installs deps (auto-detects MLX for Apple Silicon or GGUF for x86_64)
just setup      # downloads Phi-4-mini model (~2.4 GB, one-time)
```

## Dev Workflow

```bash
just check      # lint + format check + typecheck — must pass before committing
just test       # run tests
just coverage   # tests with coverage report (90%+ required for new code)
just eval       # evaluate rules against bundled test cases
just fmt        # auto-format code
```

## Before Opening a PR

1. `just check` must pass — no lint, format, or typecheck failures
2. `just coverage` must pass — 90%+ line coverage on new code
3. Update `CHANGELOG.md` under the `[Unreleased]` section
4. Keep PRs focused — one concern per PR

## Submitting a PR

- Reference the relevant issue in your PR description
- CI runs lint + typecheck + tests on macOS (MLX backend) and Linux (GGUF backend) — both need to pass
- Smaller PRs get reviewed faster

## Writing Rules

Rules live in `examples/rules/`. New bundled rules need:
- A `.yaml` rule file following the existing format
- Test cases in `examples/tests/` covering at least the positive (fires) and negative (doesn't fire) cases
- `just eval-rule <rule-name>` passing cleanly

See the existing rules as templates. The `examples/README.md` covers the full rule schema.

## Code of Conduct

This project follows the [Contributor Covenant 3.0](CODE_OF_CONDUCT.md).
