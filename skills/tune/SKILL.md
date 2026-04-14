---
name: vaudeville:tune
description: >
  Tune a single vaudeville SLM rule to meet precision/recall targets. Runs
  `vaudeville tune <rule>` which uses Optuna to search the example toggle space,
  evaluates on a held-out split, and reports a pass/fail verdict. Use when the
  user says "tune rule", "fix rule accuracy", "improve rule precision", "reduce
  false positives", "recall is too low", "tune violation-detector", or wants to
  iterate on an SLM rule prompt until it meets accuracy targets.
model: sonnet
context: fork
allowed-tools: Bash(uv:*), Read
---

# tune

Shell out to `vaudeville tune` and report the verdict.

## Arguments

First argument is the rule name (required). Optional flags after:
- `--p-min F` — minimum precision (default 0.95)
- `--r-min F` — minimum recall (default 0.80)
- `--budget N` — max Optuna trials (default 15)
- `--author` — enable LLM candidate authoring
- `--no-daemon` — force in-process MLX backend

## Workflow

Run the tune command:

```bash
uv run python -m vaudeville.tune.cli <rule> [flags]
```

The command outputs an 8-12 line verdict with tune/held-out metrics,
pool size, best example IDs, study URI, and prompt diff path.

Exit codes: 0 = pass, 1 = fail-but-completed, 2 = harness error.

Report the verdict output to the user verbatim. If exit code is 1,
suggest reviewing the diff file and running with `--author` or
increasing `--budget`.
