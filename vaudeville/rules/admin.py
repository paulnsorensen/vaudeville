"""Rule admin helpers: locate, list, and edit rule files on disk.

Mirrors the search order of the old `vaudeville/core/rules.py` admin
helpers -- global then project -- since these operate on user-writable
rule files, not the read-only bundled examples.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .loader import load_rule_file, project_rules_dir, user_rules_dir
from .models import VALID_TIERS, DecideRule, RewriteRule


def rules_search_path(project_root: str | None = None) -> list[str]:
    """Directories that exist, in priority order: global (user) then project."""
    dirs: list[str] = []
    if (global_dir := user_rules_dir()) is not None:
        dirs.append(global_dir)
    if (project_dir := project_rules_dir(project_root)) is not None:
        dirs.append(project_dir)
    return dirs


def locate_all_rule_files(
    rule_name: str, project_root: str | None = None
) -> list[Path]:
    """Return every candidate rule file path that exists, in the reverse of
    `rules_search_path` order: project first, then global (user).
    """
    candidates: list[Path] = []
    if (project_dir := project_rules_dir(project_root)) is not None:
        proj_rules = Path(project_dir)
        candidates += [
            proj_rules / f"{rule_name}.yaml",
            proj_rules / f"{rule_name}.yml",
        ]
    if (global_dir := user_rules_dir()) is not None:
        home_rules = Path(global_dir)
        candidates += [
            home_rules / f"{rule_name}.yaml",
            home_rules / f"{rule_name}.yml",
        ]
    return [p for p in candidates if p.exists()]


def locate_rule_file(rule_name: str, project_root: str | None = None) -> Path:
    """Find a rule YAML; searches project then home, raises if absent."""
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
    """Return (rule, source_dir) pairs; project-level rules override global by name."""
    seen: dict[str, tuple[DecideRule | RewriteRule, str]] = {}
    for rules_dir in rules_search_path(project_root):
        try:
            filenames = os.listdir(rules_dir)
        except OSError:
            continue
        for filename in filenames:
            if not filename.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(rules_dir, filename)
            try:
                rule = load_rule_file(path)
            except Exception:
                continue
            if rule is None:
                continue
            seen[rule.name] = (rule, rules_dir)
    return list(seen.values())


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
