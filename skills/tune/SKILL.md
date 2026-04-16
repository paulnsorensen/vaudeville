---
name: vaudeville:tune
description: >
  Tune a single vaudeville SLM rule to meet precision/recall/f1 targets. Runs
  `vaudeville tune <rule>` which launches an autonomous agent loop to iteratively
  improve the rule's prompt until it meets accuracy thresholds. Use when the
  user says "tune rule", "fix rule accuracy", "improve rule precision", "reduce
  false positives", "recall is too low", "tune violation-detector", or wants to
  iterate on an SLM rule prompt until it meets accuracy targets.
model: sonnet
context: fork
allowed-tools: Bash(uv:*), Read
---

# tune

Shell out to `vaudeville tune` and report progress.

## Arguments

First argument is the rule name (required). Optional flags after:
- `--p-min F` — minimum precision (default 0.95)
- `--r-min F` — minimum recall (default 0.80)
- `--f1-min F` — minimum F1 (default 0.85)

## Workflow

Run the tune command:

```bash
uv run python -m vaudeville tune <rule> [flags]
```

The command launches an autonomous agent that iteratively:
1. Evaluates the current rule against test cases
2. Analyzes misclassified cases
3. Improves the prompt (examples, criteria, threshold)
4. Re-evaluates until thresholds are met

Progress is tracked in `tune-results.tsv` and git commits.

Exit codes: 0 = thresholds met, 1 = incomplete, 2 = error.

Report the tune progress to the user. If the agent fails to meet
thresholds after many iterations, suggest manual prompt review.
