"""YAML rule file loading with layered directory resolution.

Layers, lowest to highest priority: bundled `examples/rules/`, user
`~/.vaudeville/rules/`, project `.vaudeville/rules/`. Later layers override
earlier ones by rule name. Invalid files are skipped and logged; the rest
still load (AC-1).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from .models import DecideRule, RewriteRule, RuleSet, parse_rule

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def rule_filenames(rules_dir: str) -> list[str]:
    try:
        names = os.listdir(rules_dir)
    except OSError:
        return []
    return sorted(name for name in names if name.endswith((".yaml", ".yml")))


def load_rule_file(path: str | Path) -> DecideRule | RewriteRule | None:
    """Load and validate a single YAML rule file. Returns None for drafts."""
    with open(path) as f:
        data: Any = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Rule file must be a YAML mapping, got {type(data).__name__}: {path}"
        )
    if data.get("draft"):
        return None
    if "context" in data:
        name = data.get("name", "?")
        raise ValueError(
            f"rule {name!r}: rule-level `context:` key is no longer supported "
            "(rule-level context injection is dropped from the spec)"
        )
    return parse_rule(data)


def _attempted_rule_name(path: str) -> str | None:
    """Peek at the raw YAML `name:` of a rule file, before validation."""
    try:
        with open(path) as f:
            data: Any = yaml.safe_load(f)
    except Exception:
        return None
    if isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str):
            return name
    return None


def load_rules(rules_dir: str) -> dict[str, DecideRule | RewriteRule]:
    """Load every valid rule in a directory; invalid rules are skipped and logged."""
    rules, _attempted = load_rules_with_attempted(rules_dir)
    return rules


def load_rules_with_attempted(
    rules_dir: str,
) -> tuple[dict[str, DecideRule | RewriteRule], dict[str, str]]:
    """Load valid rules, plus the raw `name:` attempted by every file,
    including ones that failed validation (needed so an invalid rule
    still claims its name and blocks a same-named rule in a later layer).
    """
    rules: dict[str, DecideRule | RewriteRule] = {}
    attempted: dict[str, str] = {}
    for filename in rule_filenames(rules_dir):
        path = os.path.join(rules_dir, filename)
        attempted_name = _attempted_rule_name(path)
        try:
            rule = load_rule_file(path)
        except Exception as exc:
            logger.warning("[vaudeville] Failed to load rule %s: %s", filename, exc)
            if attempted_name:
                attempted[attempted_name] = filename
            continue
        if rule is None:
            continue
        rules[rule.name] = rule
        attempted[rule.name] = filename
    return rules, attempted


def bundled_rules_dir() -> str | None:
    """Locate the plugin-bundled examples/rules directory, if present."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", str(_PACKAGE_ROOT))
    candidate = os.path.join(plugin_root, "examples", "rules")
    return candidate if os.path.isdir(candidate) else None


def user_rules_dir() -> str | None:
    """Locate the user-global ~/.vaudeville/rules directory, if present."""
    candidate = os.path.join(os.path.expanduser("~"), ".vaudeville", "rules")
    return candidate if os.path.isdir(candidate) else None


def project_rules_dir(project_root: str | None) -> str | None:
    """Locate the project's .vaudeville/rules directory, if present."""
    if not project_root:
        return None
    candidate = os.path.join(project_root, ".vaudeville", "rules")
    return candidate if os.path.isdir(candidate) else None


def rule_layers(project_root: str | None = None) -> list[tuple[str, str]]:
    """(directory, layer) pairs that exist, in layering order:
    bundled -> user -> project."""
    candidates = (
        (bundled_rules_dir(), "bundled"),
        (user_rules_dir(), "user"),
        (project_rules_dir(project_root), "project"),
    )
    return [(rules_dir, layer) for rules_dir, layer in candidates if rules_dir]


def layered_search_path(project_root: str | None = None) -> list[str]:
    """Directories that exist, in layering order: bundled -> user -> project."""
    return [rules_dir for rules_dir, _layer in rule_layers(project_root)]


class ResolvedRule(NamedTuple):
    """An active rule, the layer that owns it, and the file it came from."""

    rule: DecideRule | RewriteRule
    layer: str
    path: str


def resolve_rules_layered(project_root: str | None = None) -> dict[str, ResolvedRule]:
    """Resolve every layer by rule name; later layers override earlier ones.

    Bundled and user rules may override an earlier layer of the same name
    (AC-1). A project-layer rule sharing a name already owned by the
    bundled or user layer is refused: it is skipped and logged, so a
    project cannot silently shadow a trusted rule.
    """
    resolved: dict[str, ResolvedRule] = {}
    owner: dict[str, str] = {}
    for rules_dir, layer_name in rule_layers(project_root):
        kept, attempted = load_rules_with_attempted(rules_dir)
        for name, filename in attempted.items():
            if layer_name == "project" and name in owner and owner[name] != "project":
                logger.warning(
                    "[vaudeville] Skipping project rule %r in %s (file %s): name "
                    "already owned by the %s layer",
                    name,
                    rules_dir,
                    filename,
                    owner[name],
                )
                continue
            owner[name] = layer_name
            rule = kept.get(name)
            if rule is None:
                continue
            if layer_name == "project" and _unscoped_rewrite(rule):
                logger.warning(
                    "[vaudeville] Skipping project rule %r in %s (file %s): a "
                    "project rewrite rule needs a matcher that is not an MCP tool",
                    name,
                    rules_dir,
                    filename,
                )
                continue
            resolved[name] = ResolvedRule(
                rule, layer_name, os.path.join(rules_dir, filename)
            )
    return resolved


def load_rules_layered(project_root: str | None = None) -> RuleSet:
    """Load rules from every layer, uncached, with the ownership rule of
    `resolve_rules_layered`."""
    merged = {
        name: entry.rule for name, entry in resolve_rules_layered(project_root).items()
    }
    return RuleSet(rules=tuple(_drop_dangling_refs(merged).values()))


_TOOL_EVENTS = ("PreToolUse", "PostToolUse")


def _unscoped_rewrite(rule: DecideRule | RewriteRule) -> bool:
    """A project rewrite with an MCP matcher, or with no matcher on a tool
    event, can reach tools whose input the target guard cannot vet. A
    rewrite on an event with no tool input only downgrades to feedback."""
    if not isinstance(rule, RewriteRule):
        return False
    if rule.matcher is None:
        return rule.event in _TOOL_EVENTS
    return "mcp__" in rule.matcher


_REF_TYPES: dict[str, type[DecideRule] | type[RewriteRule]] = {
    "escalate": DecideRule,
    "rewrite": RewriteRule,
}


def _dangling_ref(
    rule: DecideRule, rules: dict[str, DecideRule | RewriteRule]
) -> str | None:
    for action in rule.on.values():
        expected = _REF_TYPES.get(action.action)
        if expected is None or action.rule is None:
            continue
        if not isinstance(rules.get(action.rule), expected):
            return f"{action.action} -> {action.rule!r}"
    return None


def _drop_dangling_refs(
    rules: dict[str, DecideRule | RewriteRule],
) -> dict[str, DecideRule | RewriteRule]:
    """Skip decide rules whose escalate/rewrite reference is missing or of the
    wrong type. Repeat until stable, because a skipped rule can leave another
    reference dangling."""
    kept = dict(rules)
    changed = True
    while changed:
        changed = False
        for name, rule in list(kept.items()):
            if not isinstance(rule, DecideRule):
                continue
            ref = _dangling_ref(rule, kept)
            if ref is not None:
                logger.warning(
                    "[vaudeville] Skipping rule %r: reference %s does not "
                    "resolve to a rule of the right type",
                    name,
                    ref,
                )
                del kept[name]
                changed = True
    return kept
