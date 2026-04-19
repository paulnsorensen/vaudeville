"""Structured examples embedded in rule prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .rules import Rule


@dataclass
class Example:
    id: str
    input: str
    label: str
    reason: str


def _format_examples(examples: list[Example]) -> str:
    blocks = [
        f"{ex.input}\nVERDICT: {ex.label}\nREASON: {ex.reason}" for ex in examples
    ]
    return "\n\n".join(blocks)


def render_prompt(
    rule: Rule,
    example_ids: list[str] | None = None,
) -> str:
    """Render the prompt template with selected examples filled in.

    When example_ids is None, all rule examples are used.
    When provided, selects from both examples and candidates by ID.
    """
    if "{{ examples }}" not in rule.prompt:
        return rule.prompt
    if example_ids is None:
        selected = rule.examples
    else:
        id_set = set(example_ids)
        pool = rule.examples + rule.candidates
        selected = [ex for ex in pool if ex.id in id_set]
    return rule.prompt.replace("{{ examples }}", _format_examples(selected))


def _parse_examples(raw: Any) -> list[Example]:
    """Parse a list of raw YAML example dicts into Example objects."""
    if not isinstance(raw, list):
        raise ValueError(f"examples must be a list, got {type(raw).__name__}")
    required = ("id", "input", "label", "reason")
    examples: list[Example] = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            continue
        missing = [k for k in required if k not in e]
        if missing:
            raise ValueError(
                f"examples[{i}] missing required keys: {', '.join(missing)}"
            )
        examples.append(
            Example(
                id=str(e["id"]),
                input=str(e["input"]),
                label=str(e["label"]),
                reason=str(e["reason"]),
            )
        )
    return examples
