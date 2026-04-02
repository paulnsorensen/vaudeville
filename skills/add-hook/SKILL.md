---
name: add-hook
description: >
  Unified hook creation entry point that routes natural language descriptions
  to the right specialist agent. Use when the user wants to enforce behavior
  in Claude Code sessions. Trigger when the user says "add a hook", "create a
  hook", "new hook", "enforce X", "guard against X", "block X", "prevent X",
  "detect when Claude does X", "catch X", "stop Claude from X", "add
  enforcement", "quality gate for X", or describes any behavior to enforce.
  This skill routes to vaudeville:hard-hook-writer (JS/Python/Bash) or vaudeville:slm-rule-writer
  (SLM/YAML) based on whether the enforcement is structural or semantic.
  Also trigger when the user says "add a rule", "new rule", "create a
  detector", "SLM rule for X", or describes behavior requiring semantic
  classification. Do NOT use for suggesting hooks from usage data (use
  vaudeville:hook-suggester), querying session analytics (use vaudeville:session-analytics),
  checking daemon status (use `vaudeville:status` skill), or debugging existing hooks.
model: sonnet
context: fork
allowed-tools: Agent, Read, Glob, Grep
---

# add-hook

Unified entry point for creating hooks. Accepts natural language descriptions
and routes to the correct specialist agent.

## Flow

### Step 1: Read the user's description

The user describes what they want to enforce in natural language:
- "stop Claude from deferring work to follow-up PRs"
- "block rm -rf"
- "catch when Claude hedges"

### Step 2: Route using decision logic

Analyze the description and route to the correct specialist.

**Route to `vaudeville:slm-rule-writer` agent when:**
- The check requires understanding intent or context
- Simple regex would have high false positives
- Content being checked is natural language (responses, PR comments, commit messages)
- Classification is nuanced (hedging vs. factual uncertainty)
- Keywords: detect, catch, tone, quality, hedging, dismissal, deferral, sycophancy,
  completeness, "when Claude does X"

**Route to `vaudeville:hard-hook-writer` agent when:**
- The check is structural (file paths, command patterns, JSON fields)
- A regex or string match is sufficient
- Speed matters (structural hooks are <100ms, SLM is 1-5s)
- Keywords: block, prevent, guard, format, inject, auto-run, command, file, path, pattern

**Ask for clarification when ambiguous:**
If the request could reasonably go either way, ask:

> This could be implemented as:
> - **Fast regex hook** (~100ms) — catches exact patterns but may miss edge cases
> - **SLM rule** (1-5s) — understands intent and catches variations
>
> Which fits your use case?

### Step 3: Surface the tradeoff, then spawn the right agent

Before spawning, tell the user which type was chosen and why:

- "Routing to **vaudeville:slm-rule-writer** because detecting [X] requires
  understanding intent — a regex would miss variations."
- "Routing to **vaudeville:hard-hook-writer** because blocking [X] is a structural
  pattern match — fast and deterministic."
- "Hooks are runtime-enforced — Claude cannot override them, unlike
  CLAUDE.md instructions."

Then spawn the named agent:

**For SLM rules:**

```
Agent(
  subagent_type: "vaudeville:slm-rule-writer",
  description: "Create SLM rule: <short summary>",
  prompt: "Create a vaudeville rule for: <user's description>.
    The plugin root is <CLAUDE_PLUGIN_ROOT path>.
    Read existing rules in rules/ for style reference."
)
```

**For JS/bash hooks:**

```
Agent(
  subagent_type: "vaudeville:hard-hook-writer",
  description: "Create JS hook: <short summary>",
  prompt: "Create a hook for: <user's description>.
    Target location: <user or plugin scope>.
    Read existing hooks for style reference."
)
```

The sub-agent does all the heavy lifting (file I/O, eval, registration).
This skill only does routing and tradeoff communication.

## Routing Decision Table

| Signal in user description | Route | Reason |
|---------------------------|-------|--------|
| "hedging", "sycophancy", "dismissal" | vaudeville:slm-rule-writer | Semantic classification |
| "deferral", "follow-up PR", "separate commit" | vaudeville:slm-rule-writer | Intent detection |
| "completeness", "TODO", "unfinished" | vaudeville:slm-rule-writer | Context understanding |
| "tone", "quality", "when Claude does X" | vaudeville:slm-rule-writer | Natural language |
| "block command X", "prevent rm" | vaudeville:hard-hook-writer | Exact pattern match |
| "guard file X", "protect path" | vaudeville:hard-hook-writer | File path check |
| "format after write", "auto-run" | vaudeville:hard-hook-writer | Structural automation |
| "inject context", "add to prompt" | vaudeville:hard-hook-writer | Context injection |
| "block force push", "no --no-verify" | vaudeville:hard-hook-writer | Command argument match |

## What This Skill Does NOT Do

- Query session analytics (use `vaudeville:session-analytics`)
- Suggest hooks from usage data (use `vaudeville:hook-suggester`)
- Check daemon status (use `vaudeville:status` skill)
- Modify existing hooks without explicit approval

## UX Principles

1. **Lead with symptoms, not mechanisms** — "What behavior are you trying to
   stop?" beats "which event type?"
2. **Inline testability** — every hook created must include a standalone test
   command (enforced by the specialist agents)
3. **Surface the tradeoff** — always explain which type was chosen (JS vs SLM)
   and why
4. **Hooks vs instructions** — surface at creation time: "Hooks are
   runtime-enforced — Claude cannot override them, unlike CLAUDE.md instructions"

## Gotchas

- If the vaudeville daemon isn't running, vaudeville:slm-rule-writer will create the
  rule but it won't fire at runtime — remind user to run `/vaudeville:status`
  to verify the daemon is healthy
- Ambiguous requests that contain BOTH structural and semantic signals
  (e.g., "block sloppy commit messages") should lean SLM — regex on natural
  language content has high false-positive rates
- If the user explicitly says "JS hook" or "SLM rule", routing is trivial —
  skip the tradeoff explanation and spawn directly
