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
from typing import Any

import yaml

from .models import DecideRule, RewriteRule, RuleSet, parse_rule

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _rule_filenames(rules_dir: str) -> list[str]:
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


def load_rules(rules_dir: str) -> dict[str, DecideRule | RewriteRule]:
    """Load every valid rule in a directory; invalid rules are skipped and logged."""
    rules: dict[str, DecideRule | RewriteRule] = {}
    for filename in _rule_filenames(rules_dir):
        path = os.path.join(rules_dir, filename)
        try:
            rule = load_rule_file(path)
        except Exception as exc:
            logger.warning("[vaudeville] Failed to load rule %s: %s", filename, exc)
            continue
        if rule is None:
            continue
        rules[rule.name] = rule
    return rules


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


def layered_search_path(project_root: str | None = None) -> list[str]:
    """Directories that exist, in layering order: bundled -> user -> project."""
    dirs: list[str] = []
    for candidate in (
        bundled_rules_dir(),
        user_rules_dir(),
        project_rules_dir(project_root),
    ):
        if candidate:
            dirs.append(candidate)
    return dirs


def load_rules_layered(project_root: str | None = None) -> RuleSet:
    """Load rules from every layer, uncached; later layers override by name.

    Bundled and user rules may override an earlier layer of the same name
    (AC-1). A project-layer rule sharing a name already owned by the
    bundled or user layer is refused: it is skipped and logged, so a
    project cannot silently shadow a trusted rule.
    """
    layers: list[tuple[str, str]] = []
    if (bundled := bundled_rules_dir()) is not None:
        layers.append((bundled, "bundled"))
    if (user := user_rules_dir()) is not None:
        layers.append((user, "user"))
    if (project := project_rules_dir(project_root)) is not None:
        layers.append((project, "project"))

    merged: dict[str, DecideRule | RewriteRule] = {}
    owner: dict[str, str] = {}
    for rules_dir, layer_name in layers:
        for name, rule in load_rules(rules_dir).items():
            if layer_name == "project" and name in owner and owner[name] != "project":
                logger.warning(
                    "[vaudeville] Skipping project rule %r in %s: name already "
                    "owned by the %s layer",
                    name,
                    rules_dir,
                    owner[name],
                )
                continue
            merged[name] = rule
            owner[name] = layer_name
    return RuleSet(rules=tuple(merged.values()))
