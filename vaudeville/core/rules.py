"""YAML rule loader.

Uses PyYAML — only imported by daemon and eval, NOT by hook entry points.
"""
from __future__ import annotations

import logging
import os
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
        self, input_data: dict[str, object], plugin_root: str = "",
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
                    logging.warning("[vaudeville] Cannot read context file: %s", file_path)
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
