# Example Rules

A small, opinionated set of starter rules. Each one is **tuned to ≥85% precision** against its eval test cases and **ships at `tier: warn`** — they nudge but do not block.

If you want hard prevention, promote them to `tier: block` on your own install once you have runtime data backing it. See [Promoting to block](#promoting-to-block) below.

## Activating Rules

**Global** (all projects):
```bash
cp examples/rules/*.yaml ~/.vaudeville/rules/
```

**Per-project** (overrides global):
```bash
mkdir -p .vaudeville/rules/
cp examples/rules/*.yaml .vaudeville/rules/
```

After copying, run `vaudeville list` to confirm.

## Why so few?

Vaudeville started with a larger example set, most of which we removed because they didn't survive a design audit. The principle: **a rule must change behavior at some reachable tier, or it's performance theater.** Past-tense violations the SLM sees only after the damage is done (turn already wasted, summary already written, sycophantic opener already said) cannot be fixed by a `Stop` hook — block tier just makes the next turn worse, and warn tier is a memo into the void.

The shipped set is what survived. See `skills/rule-audit/SKILL.md` for the framework, or run `/rule-audit` against your own rule directory.

## Included Rules

| Rule | Event | Tier | What it catches | Recoverable in next turn? |
|------|-------|------|-----------------|---------------------------|
| `deferral-detector` | PreToolUse | warn | PR review replies that defer to follow-up PRs / future tickets | Tool runs at warn; block tier prevents the comment posting |
| `git-gate` | Stop | warn | Asking permission to commit/push when work is clearly done | Yes — Claude commits in the continuation |

Both target violations that **can be corrected on the next turn**, which is what makes a `Stop`-event rule worth the inference cost. If you want to write your own, apply the same filter — see [Designing new rules](#designing-new-rules).

## Tier model

The `tier` field is the single switch — one value, one behavior.

| Tier | What happens on a violation |
|------|-----------------------------|
| `disabled` | Rule is loaded but never evaluated |
| `shadow` | Verdict logged to stderr/telemetry only; invisible to Claude and you. Used for tuning. Continues evaluating subsequent rules. |
| `log` | Prints to stderr only; no user-visible output. Like `shadow` but terminal (subsequent rules don't run). |
| `warn` | 🪫 systemMessage shown to you and injected into next-turn context. Default for these examples. |
| `block` | Action is rejected: PreToolUse blocks the tool call; Stop forces Claude to keep working. |

## Promoting to block

The shipped rules are at `warn` because **eval-time precision and runtime precision can differ** — your sessions don't look like the eval test cases. Run rules in shadow or warn first, accumulate real-world samples, then promote.

The promotion ladder (enforced by `/tier-advisor`):

| Promotion | Min samples | Min precision | Violation rate |
|-----------|-------------|---------------|----------------|
| shadow → warn | ≥50 | ≥70% | 2%–40% |
| warn → block | ≥200 | ≥85% | 5%–30% |

Workflow:

```bash
# After you've used the rules for a while…
/tier-advisor                  # see which rules have data supporting promotion
/rule-admin promote <name>     # apply the recommendation
```

If the data doesn't support promotion, leave the rule at warn — that's working as designed, not a failure.

## Rule Format

```yaml
type: decide                # decide | rewrite
name: my-rule                # unique identifier, matches the filename
event: Stop                  # Claude Code hook event to trigger on
matcher: "*"                 # optional tool-name matcher (PreToolUse/PostToolUse)
model: anthropic:claude-haiku-4-5   # optional; defaults to config default_model
prompt: |                    # few-shot classification prompt
  Classify as "violation" or "clean".
  ...
outcomes: [violation, clean] # labels the model may return
reasons:                     # optional map of outcome -> canned reason text
  violation: "explain why"
"on":                         # outcome -> action
  violation: block
tier: warn                   # disabled | shadow | log | warn | block
draft: false                 # true skips loading while iterating
test_cases:                  # eval fixtures for `uv run python -m vaudeville.eval`
  - text: "example violation text"
    outcome: violation
  - text: "example clean text"
    outcome: clean
```

A `type: rewrite` rule replaces `outcomes`/`on`/`reasons` with `target` (a
list of `tool_input.*` field paths to rewrite) and has no `test_cases`.

## Designing new rules

Before writing a YAML, run the impact filter:

1. **Which event will catch this?** (PreToolUse / PostToolUse / Stop / UserPromptSubmit)
2. **Has the damage already happened by then?** If yes, the rule must either `tier: block` (force a corrective continuation) or move earlier in the lifecycle.
3. **What's the highest reachable tier where this changes behavior?** If even `tier: block` can't fix the violation usefully (e.g., "you said 'Great question!'" — can't unsay it), don't write the rule.
4. **Is the pattern structural?** Terminal regex like "Shall I…?" should be a hard hook (≤100ms) instead of an SLM rule (1-5s).

Use `vaudeville:add-hook` to write a semantic rule or route a structural hook. Apply this filter before writing either one.

## Tooling

| When you want to… | Use |
|-------------------|-----|
| Add a new rule | `vaudeville:add-hook` |
| Tune an existing rule against test cases | Edit the rule, then run `uv run python -m vaudeville.eval --rule <name>` |
| Check eval accuracy | `uv run python -m vaudeville.eval --rule <name>` |
| Promote/demote based on runtime data | `/tier-advisor` then `/rule-admin` |
| Audit rule design (find useless rules) | `/rule-audit` |
| Suggest hooks from your session history | `/hook-suggester` |
