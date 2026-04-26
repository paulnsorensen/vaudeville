---
name: vaudeville:rule-admin
description: >
  Promote, demote, or edit vaudeville rule tiers. Use when the user says
  "promote rule", "demote rule", "move rule to warn", "move rule to block",
  "rule-admin", "change tier", "promote to warn", "promote to block",
  "demote to shadow", "delete rule", "enable rule", "disable rule",
  or invokes /rule-admin. Typically used after /tier-advisor produces
  recommendations. Do NOT use for analysis — use /tier-advisor for that.
model: sonnet
allowed-tools: Read, Bash(vaudeville:*)
---

# rule-admin

Applies tier changes to vaudeville rule YAML files via the `vaudeville` CLI.
All mechanical operations (locate, read, write, delete) are handled by the CLI.

## Available CLI commands

```bash
vaudeville list [--tier disabled|shadow|log|warn|block] [--event Stop] [--json]
vaudeville show <name> [--json]
vaudeville promote <name>       # shadow → warn → block
vaudeville demote <name>        # block → warn → shadow
vaudeville enable <name>        # restore from disabled
vaudeville disable <name>       # disable (saves previous tier)
vaudeville delete <name> [--yes]
vaudeville path <name>
vaudeville validate [name]
```

## Input

A rule name and action. If not provided, ask. Valid actions: `promote`,
`demote`, `enable`, `disable`, `delete`.

## Steps

### 1. Confirm the rule exists

```bash
vaudeville show <rule-name>
```

Show the user the current tier, event, and threshold. If not found, stop.

### 2. Apply the change

Use the matching CLI command. Examples:

```bash
vaudeville promote sycophancy-detector
vaudeville demote hedging-detector
vaudeville disable stale-rule
vaudeville enable restored-rule
vaudeville delete old-rule --yes
```

`promote` and `demote` step one tier at a time (shadow↔warn↔block).
Use `disable` / `enable` for the disabled tier — they preserve the previous
tier in a sidecar comment so `enable` can restore it.

### 3. Confirm

Run `vaudeville show <rule-name>` again and report the new tier to the user.

## Gotchas

- `delete` prompts for confirmation unless `--yes` is passed. In non-interactive
  contexts always pass `--yes`.
- If a rule exists in both project and home, `delete` will ask which location
  to remove.
- `promote` / `demote` refuse to move a disabled rule — use `enable` first.
- Rules search order: project `.vaudeville/rules/` first, then `~/.vaudeville/rules/`.
