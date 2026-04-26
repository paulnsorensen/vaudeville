---
name: vaudeville:rule-audit
description: >
  Audit existing vaudeville rules for design-level usefulness. Distinct from
  /tier-advisor (which audits eval data for promotion). This skill asks: does
  the rule's event × tier combination produce useful behavior change *at any
  reachable tier*? Surfaces rules that are useless by design — rules whose
  violations cannot be acted on in a future turn, rules stuck at SLM model
  ceilings, rules whose max-useful tier is below their current tier, rules
  duplicated by other rules, and rules that should be hard hooks instead of
  SLM rules. Use when the user says "audit my rules", "find useless rules",
  "are my rules useful", "review rule design", "rule audit", "which rules
  should I delete", "do my rules make sense", "design audit", or invokes
  /rule-audit. Do NOT use for promotion analysis (use /tier-advisor) or
  modifying tiers (use /rule-admin).
model: sonnet
context: fork
allowed-tools: Read, Glob, Grep, Bash
---

# rule-audit

Design-level audit of vaudeville rules. Answers: "Even if perfectly tuned,
would this rule do anything useful?"

## Why this is distinct from /tier-advisor

| Skill | Question | Inputs |
|-------|----------|--------|
| `/tier-advisor` | Does the **eval data** support promoting/demoting this rule? | Telemetry, precision/recall counts |
| `/rule-audit` (this) | Does the rule's **design** produce useful behavior at any tier it could reach? | Rule YAML (event, tier, prompt, ceiling notes) |

`/tier-advisor` answers the data question; `/rule-audit` answers the architecture question. A rule can pass tier-advisor (data looks great) and still fail rule-audit (the rule fires too late to matter, even if precision is 100%).

## The audit framework

For every rule, compute three things:

### 1. Recoverability

Can the violation be addressed in a future turn?

- **Yes**: Stop+block forces a corrective continuation; the next turn is meaningfully different (e.g., Claude commits the work, fetches docs, executes the plan).
- **No**: The damage is at-the-start or unretractable (sycophantic opener, trailing summary already written, turn already wasted). Block tier produces a worse continuation than the original violation.

### 2. Max useful tier

What's the highest tier where this rule changes behavior usefully?

| Recoverability | Max useful tier | Notes |
|----------------|-----------------|-------|
| Strong (continuation fixes the violation) | `block` | Hard prevention |
| Weak (next-turn memo only, no in-turn fix) | `warn` | Warn-ceiling rule |
| None (block makes it worse, warn is noise) | none | **Useless rule** |

### 3. Model ceiling

Search the YAML for `STUCK-AT-MODEL-CEILING` comments. If present:
- Phi-4-mini cannot tune past the documented F1.
- If the documented precision can clear the promotion threshold (≥70% for warn, ≥85% for block), the rule is shippable but capped.
- If precision is below the warn threshold, the rule is a permanent shadow — recommend converting to a hard hook (regex/JS) or deletion.

## Verdict categories

| Verdict | Trigger | Action |
|---------|---------|--------|
| `KEEP` | Useful destination tier reachable; on-track in shadow/warn | None |
| `PROMOTE` | Eval data supports moving up the ladder | Run `/tier-advisor`, then `/rule-admin` |
| `DOWNGRADE` | Currently above its max-useful tier (e.g., block on a non-recoverable violation) | `/rule-admin demote` |
| `CONVERT` | SLM ceiling below promotion threshold; pattern is structural | Replace via `vaudeville:hard-hook-writer` |
| `DELETE` | No useful destination tier exists OR currently disabled with no revival plan OR superseded by another rule | `vaudeville delete <name> --yes` |

## Workflow

### 1. Enumerate rules

```bash
uv run vaudeville list
```

For each rule, also `Read` the YAML to extract the prompt's task statement and check for `# STUCK-AT-MODEL-CEILING` comments.

### 2. Apply the framework per rule

Build a table:

| Rule | Event | Current tier | Recoverable? | Max useful tier | Model ceiling? | Verdict |

### 3. Cross-check against duplicates

A rule is also useless if another rule covers the same pattern. Check the prompts pairwise — if two rules would fire on the same text, propose merging.

### 4. Present findings

Group by verdict. For each `DELETE` / `CONVERT` / `DOWNGRADE`, give the specific reason (not just "useless"). For `KEEP`, note whether `/tier-advisor` should be run next.

End with: "Apply these recommendations? (all / numbers / none)"

### 5. Execute approved actions

- `DELETE` → `uv run vaudeville delete <name> --yes`
- `DOWNGRADE` → invoke `/rule-admin demote <name> <new-tier>`
- `CONVERT` → invoke `vaudeville:add-hook` with the structural pattern description
- `PROMOTE` → invoke `/tier-advisor` first, then `/rule-admin promote` if data supports

Never delete or modify without explicit approval.

## What this skill does NOT do

- Modify rules without approval
- Replace `/tier-advisor` (data analysis is its job)
- Replace `/rule-admin` (tier mutation is its job)
- Audit eval test cases (use `just eval`)

## Gotchas

- Disabled rules (`tier: disabled`) often look like KEEP candidates but should usually be `DELETE` — if they were worth running, they wouldn't be disabled. Confirm with the user before recommending deletion.
- Rules with `STUCK-AT-MODEL-CEILING` and high precision but low recall are still useful — high precision means few false positives, low recall means missed violations (acceptable for shadow → warn promotion). Don't flag these as `DELETE` unless precision is also below the promotion threshold.
- Some rules have a "warn ceiling" by design (sycophancy, trailing-summary). They're not useless, but they should never be promoted to `block`. Note the ceiling explicitly so future tier-advisor runs don't try to promote them past it.
- Rules in `~/.vaudeville/rules/_disabled/` are user-archived; don't audit them as live rules.

## Example output

```
=== Rule Audit ===

DELETE (3):
  • turn-waste-detector — Stop+warn, non-recoverable: turn already over
  • todo-smuggler — PostToolUse+shadow, file already on disk; reframe as PreToolUse hard hook
  • sycophancy-detector — disabled, no revival plan; warn-ceiling at best

CONVERT (1):
  • over-asking-detector — STUCK-AT-MODEL-CEILING (F1 58.8); pattern is structural,
    convert to hard hook on terminal "Shall I…?" / "Want me to…?"

KEEP (5):
  • git-gate — Stop+shadow, recoverable (Claude commits next turn). /tier-advisor
    when ≥50 samples
  • plan-without-execution — Stop+shadow, recoverable (Claude executes next turn)
  • scope-erosion — Stop+shadow, recoverable
  • fabricated-confidence — Stop+shadow, recoverable (Claude fetches docs next turn)
  • deferral-detector — PreToolUse+warn, useful as guardrail; consider promoting
    to block once eval data supports it

NOTE (1):
  • preexisting-fix-detector — STUCK-AT-MODEL-CEILING but precision=100%; ship as
    warn (recall ceiling 77.8% accepted)
```
