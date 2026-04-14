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
allowed-tools: Read, Edit, Write, Glob, Bash(cp:*), Bash(rm:*), Bash(ls:*)
---

# rule-admin

Applies tier changes to vaudeville rule YAML files. Handles the mechanical
steps of promoting, demoting, or deleting rules after /tier-advisor has
produced recommendations.

## Input

A rule name and action. If not provided, ask. Valid actions:

| Action | What it does |
|--------|-------------|
| `promote-to-warn` | Set `tier: warn` in `rules_dev/`, copy to `~/.vaudeville/rules/` |
| `promote-to-block` | Set `tier: block` in `rules_dev/`, copy to `~/.vaudeville/rules/` |
| `demote-to-shadow` | Set `tier: shadow` in `rules_dev/`, remove from `~/.vaudeville/rules/` |
| `delete` | Remove from both `rules_dev/` and `~/.vaudeville/rules/` (confirm first) |

## Steps

### 1. Locate the rule

Find the rule YAML file. Search in order:
1. `${CLAUDE_PLUGIN_ROOT}/rules_dev/<rule-name>.yaml`
2. `${CLAUDE_PLUGIN_ROOT}/rules/<rule-name>.yaml`
3. `~/.vaudeville/rules/<rule-name>.yaml`

If not found, tell the user and stop.

### 2. Read current state

Read the rule file and show the user:
- Current `tier:` value (or "none" if field is absent)
- Current `threshold:` value
- Rule `event:` type

### 3. Apply the change

For **promote-to-warn**:
1. Set `tier: warn` in the rule YAML
2. Copy the file to `~/.vaudeville/rules/<rule-name>.yaml`

For **promote-to-block**:
1. Set `tier: block` in the rule YAML
2. Copy the file to `~/.vaudeville/rules/<rule-name>.yaml`

For **demote-to-shadow**:
1. Set `tier: shadow` in the rule YAML
2. Remove `~/.vaudeville/rules/<rule-name>.yaml` if it exists

For **delete**:
1. Confirm with the user before proceeding
2. Remove the rule file from `rules_dev/` and `~/.vaudeville/rules/`

### 4. Confirm

Show the user what changed and where.

## Gotchas

- Always read the YAML before editing — rules may have comments or
  non-standard field ordering that blind writes would destroy
- The `~/.vaudeville/rules/` directory may not exist yet — create it
  if needed when copying
- Delete is destructive — always confirm before removing files
- Some rules exist only in `~/.vaudeville/rules/` (user-created),
  not in `rules_dev/` — handle both locations
