---
name: vaudeville:tune
description: >
  Tune a single vaudeville SLM rule to meet precision/recall targets. Runs eval,
  analyzes misclassifications (FP/FN), edits the rule prompt, and re-evaluates
  in a loop. Use when the user says "tune rule", "fix rule accuracy", "improve
  rule", "tune violation-detector", "why is my rule failing", or invokes /tune
  with a rule name. Requires the vaudeville daemon to be running for inference.
model: sonnet
context: fork
allowed-tools: Bash(uv:*), Bash(bash:*), Read, Edit, Write, Glob
---

# tune

Automated single-rule tuning loop. Target: >= 95% precision AND >= 80% recall.

## Arguments

The first argument is the rule name (e.g., `violation-detector`). Required.

Optional flags after the rule name:
- `--calibrate` — after tuning, run threshold calibration
- `--max-iterations N` — max tuning loops (default: 3)

## Prerequisites

The vaudeville daemon must be running (the eval harness needs the MLX backend).
Rules live in `rules_dev/`. Test cases live in `tests/`.

## Workflow

### Step 1: Validate the rule exists

Check that `rules_dev/<rule-name>.yaml` exists and that there's a matching
test file in `tests/` (any YAML file with `rule: <rule-name>`).

```bash
ls rules_dev/<rule-name>.yaml
```

Find the test file:
```bash
grep -l "^rule: <rule-name>" tests/*.yaml
```

If either is missing, report the error and stop.

### Step 2: Run baseline eval

Run eval for this specific rule using the project's eval wrapper:

```bash
bash ralphs/rule-tuning-v2/run-eval.sh 2>&1 | grep -E "(Evaluating <rule-name>|=== <rule-name>|Accuracy|Precision|Recall|F1|Confusion|Confidence|Misclass|actual=)"
```

If the run-eval.sh script doesn't support single-rule filtering, use:

```bash
uv run python -m vaudeville.eval --rules-dir rules_dev --rule <rule-name> 2>&1 | grep -E "(^Evaluating|^===|Accuracy|Precision|Recall|F1|Confusion|Confidence|Misclass|actual=|WARNING|ALL RULES|SOME RULES)"
```

Record the baseline precision, recall, F1, and any misclassifications.

### Step 3: Analyze misclassifications

For each misclassification from the eval output, categorize it:

- **FP (false positive)**: `actual=clean predicted=violation` — model is too aggressive.
  The text is legitimately clean but the rule prompt is triggering on it.
- **FN (false negative)**: `actual=violation predicted=clean` — model is too lenient.
  The text contains a real violation but the rule prompt isn't catching it.

Diagnosis strategy:
- Read the full rule prompt in `rules_dev/<rule-name>.yaml`
- Read the full test case text in the test file (the eval output truncates to 80 chars)
- For each FP: identify what pattern in the prompt is over-matching
- For each FN: identify what violation pattern is missing from the prompt

### Step 4: Edit the rule prompt

Based on the analysis, make targeted edits to the rule's `prompt:` field:

**For FPs (too aggressive):**
- Add a "CLEAN if..." clause that covers the FP pattern
- Add a clean example that matches the FP text pattern
- Narrow an overly broad violation criterion

**For FNs (too lenient):**
- Add or strengthen a "VIOLATION if..." clause for the missed pattern
- Add a violation example that matches the FN text pattern
- Make violation criteria more specific (not just broader)

**Budget constraints:**
- Keep total prompt under 2000 characters
- Keep examples at 4-8 total (don't exceed 8)
- If at the example budget, replace the weakest example rather than adding

After editing, read the file back to verify the edit was clean.

### Step 5: Re-run eval

Run the same eval command as Step 2. Compare against baseline:

- Did precision improve? (FPs should decrease)
- Did recall improve? (FNs should decrease)
- Did we regress on either metric?

If both metrics meet target (>= 95% P, >= 80% R), report success.

### Step 6: Loop or stop

If targets not met and iterations < max (default 3):
- Go back to Step 3 with the new misclassifications
- Each iteration should target the highest-impact misclassification

If targets not met after max iterations:
- Report final metrics
- List remaining misclassifications
- Add a YAML comment to the rule noting the limitation

### Step 7: Optional calibration

If `--calibrate` was passed and the rule has >= 20 test cases:

```bash
uv run python -m vaudeville.eval --rules-dir rules_dev --calibrate <rule-name> 2>&1 | grep -E "(Calibrated|threshold|Updated|ERROR|WARNING)"
```

Report the calibrated threshold and updated metrics.

## Output Format

Report results as a compact tuning log:

```
## Tune: <rule-name>

**Baseline**: P=XX.X% R=XX.X% F1=XX.X% (TP=N FP=N TN=N FN=N)

### Iteration 1
- FPs: N (pattern: <description>)
- FNs: N (pattern: <description>)
- Edit: <what was changed in the prompt>
- Result: P=XX.X% R=XX.X% F1=XX.X%

### Iteration 2 (if needed)
...

**Final**: P=XX.X% R=XX.X% F1=XX.X% [PASS/FAIL]
**Threshold**: X.XX (if calibrated)
```

## Gotchas

- The eval harness loads the MLX model on every run — each eval takes 30-60s.
  Don't run unnecessary iterations.
- Prompt edits that fix FPs can cause FNs and vice versa. Track both metrics
  every iteration — never optimize for one at the expense of the other.
- Test case text in eval output is truncated to 80 chars. Always read the full
  test case from the YAML file before diagnosing.
- The 2000-char prompt budget is real — Phi-4-mini degrades badly on longer prompts.
  If you're at budget, trade a weak example for a better one rather than expanding.
- Rules in `rules_dev/` are the source of truth. Never edit `examples/rules/` or
  `~/.vaudeville/rules/`.
