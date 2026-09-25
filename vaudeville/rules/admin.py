"""Rule admin helpers: locate, list, and edit rule files on disk.

These helpers use the same layered resolver as the daemon, so they show
and edit the file that the daemon runs. They operate on user-writable
rule files only, not the read-only bundled examples.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .loader import ResolvedRule, load_rule_file, resolve_active_rules, rule_layers
from .models import VALID_TIERS, DecideRule, RewriteRule

_EDITABLE_LAYERS = ("user", "project")


def rules_search_path(project_root: str | None = None) -> list[str]:
    """Directories that exist, in priority order: global (user) then project."""
    return [
        rules_dir
        for rules_dir, layer in rule_layers(project_root)
        if layer in _EDITABLE_LAYERS
    ]


def _active_editable(
    project_root: str | None,
) -> dict[str, ResolvedRule]:
    return {
        name: entry
        for name, entry in resolve_active_rules(project_root).items()
        if entry.layer in _EDITABLE_LAYERS
    }


def locate_all_rule_files(
    rule_name: str, project_root: str | None = None
) -> list[Path]:
    """Return every user or project file for `rule_name`. The file that the
    daemon runs comes first; other files named `<rule_name>.yaml|yml`
    follow in `rules_search_path` order.
    """
    paths: list[Path] = []
    active = _active_editable(project_root).get(rule_name)
    if active is not None:
        paths.append(Path(active.path))
    for rules_dir in rules_search_path(project_root):
        for suffix in (".yaml", ".yml"):
            candidate = Path(rules_dir) / f"{rule_name}{suffix}"
            if candidate.exists() and candidate not in paths:
                paths.append(candidate)
    return paths


def locate_rule_file(rule_name: str, project_root: str | None = None) -> Path:
    """Find the rule file that the daemon runs; raises if absent."""
    for path in locate_all_rule_files(rule_name, project_root):
        return path
    raise FileNotFoundError(f"rule file not found for {rule_name!r}")


def set_tier(rule_name: str, new_tier: str, project_root: str | None = None) -> Path:
    """Update the tier field in a rule file in-place. Returns the modified path."""
    if new_tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier {new_tier!r}, must be one of {VALID_TIERS}")
    path = locate_rule_file(rule_name, project_root)
    content = path.read_text()
    new_content, count = re.subn(
        r"^tier:\s*\S+", f"tier: {new_tier}", content, flags=re.MULTILINE
    )
    if count == 0:
        sep = "" if not content or content.endswith("\n") else "\n"
        new_content = content + sep + f"tier: {new_tier}\n"
    path.write_text(new_content)
    return path


def list_rules_with_source(
    project_root: str | None = None,
) -> list[tuple[DecideRule | RewriteRule, str]]:
    """Return (rule, source_dir) pairs for the active user and project rules."""
    return [
        (entry.rule, os.path.dirname(entry.path))
        for entry in _active_editable(project_root).values()
    ]


def get_draft_rule_names(rules_dir: str) -> set[str]:
    """Return the names of rules marked draft: true in a directory."""
    names: set[str] = set()
    try:
        filenames = os.listdir(rules_dir)
    except OSError:
        return names
    for filename in filenames:
        if not filename.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(rules_dir, filename)
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict) and data.get("draft") and "name" in data:
                names.add(str(data["name"]))
        except Exception:
            continue
    return names


def validate_rule_file(path: str | Path) -> DecideRule | RewriteRule | None:
    """Validate a rule file, raising on error. Returns None for drafts."""
    return load_rule_file(path)
