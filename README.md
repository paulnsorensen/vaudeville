# Vaudeville

SLM-powered semantic hook enforcement for [Claude Code](https://claude.ai/code). Classifies AI assistant output against YAML rules using local Phi-4-mini inference.

## How It Works

Vaudeville runs a local inference daemon (Phi-4-mini, 3.8B params, int4) that classifies Claude Code's output in real time. You write rules as YAML files with few-shot prompt templates. The daemon evaluates them on every hook event and returns block/warn/log verdicts.

**Fail-open by design** — if the daemon is down, the model isn't downloaded, or inference errors out, your session continues normally. Vaudeville never blocks you from working.

## Install

```
/plugin install vaudeville@paulnsorensen
```

Then run the one-time setup to download the model (~2.4 GB):

```
/vaudeville:setup
```

This detects your platform (Apple Silicon or x86_64) and downloads the appropriate model variant.

## Quick Start

1. Copy example rules to your global rules directory:
   ```bash
   cp ~/.claude/plugins/cache/paulnsorensen/vaudeville/*/examples/rules/*.yaml ~/.vaudeville/rules/
   ```

2. Start a new Claude Code session — the daemon launches automatically.

3. Rules fire on their configured hook events. Violations block, warn, or log depending on rule config.

## Rules

Rules live in `~/.vaudeville/rules/` (global) or `.vaudeville/rules/` (per-project, higher priority).

See [`examples/`](examples/) for starter rules and the YAML format reference.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- ~4 GB disk for the model
- Apple Silicon (MLX backend) or x86_64 (GGUF backend)

## Development

```bash
just install    # install dependencies
just check      # lint + format + typecheck
just test       # run tests
just eval       # evaluate rules against test cases
```

## License

[MIT](LICENSE)
