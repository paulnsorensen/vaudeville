"""Hook-origin label prefixed to feedback/rewrite text that reaches an agent."""

from __future__ import annotations

_LABEL_TEMPLATE = "[vaudeville hook: {rule}] "


def _label_for(rule_name: str) -> str:
    return _LABEL_TEMPLATE.format(rule=rule_name)


def with_origin_label(rule_name: str, text: str) -> str:
    """Prefix `text` with a hook-origin label naming `rule_name`, once."""
    label = _label_for(rule_name)
    if text.startswith(label):
        return text
    return f"{label}{text}"
