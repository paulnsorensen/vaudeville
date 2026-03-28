"""YAML rule loader with layered config resolution.

Rules are resolved from multiple directories in priority order:
  1. project/.vaudeville/rules/   (highest — project overrides)
  2. ~/.vaudeville/rules/          (user-global rules)
  3. <plugin_root>/rules/          (bundled defaults, lowest)

Higher-priority rules override lower-priority ones by name.

Uses PyYAML — only imported by daemon and eval, NOT by hook entry points.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any

import yaml


def _resolve_field(data: dict[str, object], path: str) -> object:
    """Resolve a dot-notation path like 'tool_input.body' from a nested dict."""
    current: object = data
    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key, "")
        else:
            return ""
    return current


@dataclass
class Rule:
    name: str
    event: str
    prompt: str
    context: list[dict[str, str]]
    labels: list[str]
    action: str
    message: str

    def format_prompt(self, text: str, context: str = "") -> str:
        return self.prompt.replace("{text}", text).replace("{context}", context)

    def resolve_context(
        self,
        input_data: dict[str, object],
        plugin_root: str = "",
    ) -> str:
        """Resolve context entries from field: (JSON path) or file: (disk path)."""
        parts: list[str] = []
        for entry in self.context:
            if "field" in entry:
                val = _resolve_field(input_data, entry["field"])
                parts.append(str(val))
            elif "file" in entry:
                file_path = entry["file"]
                if not os.path.isabs(file_path):
                    file_path = os.path.join(plugin_root, file_path)
                try:
                    with open(file_path) as f:
                        parts.append(f.read())
                except OSError:
                    logging.warning(
                        "[vaudeville] Cannot read context file: %s", file_path
                    )
        return "\n".join(parts)


def load_rules(rules_dir: str) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    try:
        filenames = os.listdir(rules_dir)
    except OSError:
        return rules

    for filename in filenames:
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue
        path = os.path.join(rules_dir, filename)
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            rule = _parse_rule(data)
            rules[rule.name] = rule
        except Exception as exc:
            logging.warning("[vaudeville] Failed to load rule %s: %s", filename, exc)

    return rules


def _find_project_root() -> str | None:
    """Find the git working tree root, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def rules_search_path(plugin_root: str) -> list[str]:
    """Build the rules directory search path (lowest → highest priority).

    Returns directories that exist. Order: bundled → global → project.
    """
    dirs: list[str] = []

    # Lowest priority: bundled defaults
    bundled = os.path.join(plugin_root, "rules")
    if os.path.isdir(bundled):
        dirs.append(bundled)

    # Mid priority: user-global
    global_dir = os.path.join(os.path.expanduser("~"), ".vaudeville", "rules")
    if os.path.isdir(global_dir):
        dirs.append(global_dir)

    # Highest priority: project-local
    project_root = _find_project_root()
    if project_root:
        project_dir = os.path.join(project_root, ".vaudeville", "rules")
        if os.path.isdir(project_dir):
            dirs.append(project_dir)

    return dirs


def load_rules_layered(plugin_root: str) -> dict[str, Rule]:
    """Load rules from all search path directories, higher priority wins."""
    merged: dict[str, Rule] = {}
    for rules_dir in rules_search_path(plugin_root):
        merged.update(load_rules(rules_dir))
    return merged


def _parse_rule(data: dict[str, Any]) -> Rule:
    return Rule(
        name=str(data["name"]),
        event=str(data.get("event", "")),
        prompt=str(data["prompt"]),
        context=[c for c in data.get("context", []) if isinstance(c, dict)],
        labels=[str(lb) for lb in data.get("labels", ["violation", "clean"])],
        action=str(data.get("action", "block")),
        message=str(data.get("message", "{reason}")),
    )
