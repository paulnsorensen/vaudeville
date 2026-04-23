"""YAML rule loader with layered config resolution.

Rules are resolved from multiple directories in priority order:
  1. project/.vaudeville/rules/   (highest -- project overrides)
  2. ~/.vaudeville/rules/          (user-global rules)

Higher-priority rules override lower-priority ones by name.

Uses PyYAML -- only imported by daemon and eval, NOT by hook entry points.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .examples import Example, _parse_examples, render_prompt
from .truncation import _truncate_for_event, prepare_text

DEFAULT_LABELS: tuple[str, ...] = ("violation", "clean")


@dataclass
class EvalCase:
    """One labeled example for rule evaluation."""

    text: str
    label: str


def sanitize_input(text: str) -> str:
    """Neutralize verdict/reason markers that could spoof parse_verdict().

    parse_verdict() matches case-insensitively, so sanitization must too.
    Zero-width space breaks the pattern match without altering visible text.
    """
    text = re.sub(r"(?i)VERDICT\s*:", lambda m: m.group().replace(":", "\u200b:"), text)
    text = re.sub(r"(?i)REASON\s*:", lambda m: m.group().replace(":", "\u200b:"), text)
    return text


def _resolve_field(data: dict[str, object], path: str) -> object:
    """Resolve a dot-notation path like 'tool_input.body' from a nested dict."""
    current: object = data
    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key, "")
        else:
            return ""
    return current


def _read_context_entry(
    entry: dict[str, str],
    input_data: dict[str, object],
    plugin_root: str,
) -> str:
    """Resolve a single context entry from field: (JSON path) or file: (disk path)."""
    if "field" in entry:
        return str(_resolve_field(input_data, entry["field"]))
    if "file" in entry:
        file_path = entry["file"]
        if not os.path.isabs(file_path):
            file_path = os.path.join(plugin_root, file_path)
        try:
            with open(file_path) as f:
                return f.read()
        except OSError:
            logging.warning("[vaudeville] Cannot read context file: %s", file_path)
    return ""


VALID_TIERS = ("shadow", "warn", "enforce", "disabled")


@dataclass
class Rule:
    name: str
    event: str
    prompt: str
    context: list[dict[str, str]]
    action: str
    message: str
    threshold: float = 0.5
    examples: list[Example] = field(default_factory=list)
    candidates: list[Example] = field(default_factory=list)
    tier: str = "enforce"
    labels: list[str] = field(default_factory=lambda: list(DEFAULT_LABELS))
    test_cases: list[EvalCase] = field(default_factory=list)

    def format_prompt(self, text: str, context: str = "") -> str:
        base = render_prompt(self)
        safe_text = sanitize_input(
            _truncate_for_event(prepare_text(text, self.event), self.event)
        )
        safe_context = sanitize_input(context) if context else ""
        return base.replace("{text}", safe_text).replace("{context}", safe_context)

    def split_prompt(self, text: str, context: str = "") -> tuple[str, int]:
        """Format prompt and return (full_prompt, prefix_len).

        prefix_len is the character index where the static prefix ends
        and the variable {text} content begins.
        """
        base = render_prompt(self)
        safe_text = sanitize_input(
            _truncate_for_event(prepare_text(text, self.event), self.event)
        )
        safe_context = sanitize_input(context) if context else ""
        prompt_with_context = base.replace("{context}", safe_context)

        if "{text}" not in prompt_with_context:
            return prompt_with_context, 0

        before, _, after = prompt_with_context.partition("{text}")
        full_prompt = before + safe_text + after
        return full_prompt, len(before)

    def resolve_context(
        self,
        input_data: dict[str, object],
        plugin_root: str = "",
    ) -> str:
        """Resolve context entries from field: (JSON path) or file: (disk path)."""
        parts = [
            _read_context_entry(entry, input_data, plugin_root)
            for entry in self.context
        ]
        return "\n".join(p for p in parts if p)


def _load_rule_file(path: str) -> Rule | None:
    """Load and parse a single YAML rule file. Returns None for draft rules."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Rule file must be a YAML mapping, got {type(data).__name__}: {path}"
        )
    if data.get("draft"):
        return None
    return parse_rule(data)


def get_draft_rule_names(rules_dir: str) -> set[str]:
    """Return the names of rules marked draft: true in a directory."""
    names: set[str] = set()
    try:
        filenames = os.listdir(rules_dir)
    except OSError:
        return names
    for filename in filenames:
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue
        path = os.path.join(rules_dir, filename)
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if data.get("draft") and "name" in data:
                names.add(str(data["name"]))
        except Exception:
            continue
    return names


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
            rule = _load_rule_file(path)
            if rule is None:
                continue
            rules[rule.name] = rule
        except Exception as exc:
            logging.warning("[vaudeville] Failed to load rule %s: %s", filename, exc)

    return rules


def rules_search_path(
    project_root: str | None = None,
) -> list[str]:
    """Build the rules directory search path (lowest -> highest priority).

    Returns directories that exist. Order: global -> project.
    """
    dirs: list[str] = []

    global_dir = os.path.join(os.path.expanduser("~"), ".vaudeville", "rules")
    if os.path.isdir(global_dir):
        dirs.append(global_dir)

    if project_root:
        project_dir = os.path.join(project_root, ".vaudeville", "rules")
        if os.path.isdir(project_dir):
            dirs.append(project_dir)

    return dirs


def load_rules_layered(
    project_root: str | None = None,
) -> dict[str, Rule]:
    """Load rules from all search path directories, higher priority wins."""
    merged: dict[str, Rule] = {}
    for rules_dir in rules_search_path(project_root):
        merged.update(load_rules(rules_dir))
    return merged


def parse_rule(data: dict[str, Any]) -> Rule:
    """Parse a raw YAML dict into a validated Rule."""
    tier = str(data.get("tier", "enforce"))
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier {tier!r}, must be one of {VALID_TIERS}")
    name = str(data["name"])
    raw_labels = data.get("labels")
    if raw_labels is not None:
        if not isinstance(raw_labels, list) or not raw_labels:
            raise ValueError(
                f"Rule {data.get('name', '?')!r}: 'labels' must be a non-empty list of strings, "
                f"got {raw_labels!r}"
            )
        labels: list[str] = [str(label) for label in raw_labels]
    else:
        labels = [str(label) for label in DEFAULT_LABELS]
    test_cases = _parse_test_cases(data.get("test_cases", []), name, labels)
    return Rule(
        name=name,
        event=str(data.get("event", "")),
        prompt=str(data["prompt"]),
        context=[c for c in data.get("context", []) if isinstance(c, dict)],
        action=str(data.get("action", "block")),
        message=str(data.get("message", "{reason}")),
        threshold=float(data.get("threshold", 0.5)),
        examples=_parse_examples(data.get("examples", [])),
        candidates=_parse_examples(data.get("candidates", [])),
        tier=tier,
        labels=labels,
        test_cases=test_cases,
    )


def locate_rule_file(rule_name: str, project_root: str | None = None) -> Path:
    """Find a rule YAML; searches project then home, raises FileNotFoundError if absent."""
    home = os.path.expanduser("~")
    candidates: list[Path] = []
    if project_root:
        proj_rules = Path(project_root) / ".vaudeville" / "rules"
        candidates += [
            proj_rules / f"{rule_name}.yaml",
            proj_rules / f"{rule_name}.yml",
        ]
    home_rules = Path(home) / ".vaudeville" / "rules"
    candidates += [home_rules / f"{rule_name}.yaml", home_rules / f"{rule_name}.yml"]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"rule file not found for {rule_name!r}")


def locate_all_rule_files(
    rule_name: str, project_root: str | None = None
) -> list[Path]:
    home = os.path.expanduser("~")
    candidates: list[Path] = []
    if project_root:
        proj_rules = Path(project_root) / ".vaudeville" / "rules"
        candidates += [
            proj_rules / f"{rule_name}.yaml",
            proj_rules / f"{rule_name}.yml",
        ]
    home_rules = Path(home) / ".vaudeville" / "rules"
    candidates += [home_rules / f"{rule_name}.yaml", home_rules / f"{rule_name}.yml"]
    return [p for p in candidates if p.exists()]


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


def list_rules_with_source(project_root: str | None = None) -> list[tuple[Rule, str]]:
    """Return (rule, source_dir) pairs; project-level rules override global by name."""
    seen: dict[str, tuple[Rule, str]] = {}
    for rules_dir in rules_search_path(project_root):
        for filename in os.listdir(rules_dir):
            if not (filename.endswith(".yaml") or filename.endswith(".yml")):
                continue
            path = os.path.join(rules_dir, filename)
            try:
                rule = _load_rule_file(path)
                if rule is None:
                    continue
                seen[rule.name] = (rule, rules_dir)
            except Exception:
                continue
    return list(seen.values())


def load_rule_file(path: str | Path) -> Rule | None:
    """Returns None for drafts."""
    return _load_rule_file(str(path))


def _parse_test_cases(raw: object, rule_name: str, labels: list[str]) -> list[EvalCase]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"Rule {rule_name!r}: test_cases must be a list, got {type(raw).__name__}"
        )
    cases: list[EvalCase] = []
    label_set = set(labels)
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"Rule {rule_name!r} test_cases[{i}]: expected mapping, got "
                f"{type(entry).__name__}"
            )
        text = entry.get("text")
        label = entry.get("label")
        if not isinstance(text, str) or not text:
            raise ValueError(
                f"Rule {rule_name!r} test_cases[{i}]: 'text' must be a non-empty string"
            )
        if not isinstance(label, str) or label not in label_set:
            raise ValueError(
                f"Rule {rule_name!r} test_cases[{i}]: 'label' must be one of "
                f"{sorted(label_set)}, got {label!r}"
            )
        cases.append(EvalCase(text=text, label=label))
    return cases
